// Behaviour tests for the subagent lifecycle: launch, concurrency queueing,
// result finalisation, failure detection, cancel, wait, status and clean.
//
// The extension talks to tmux exclusively through `pi.exec`, so a fake
// ExtensionAPI that scripts `exec` is enough to drive the whole state machine
// without spawning real tmux sessions or child pi processes.
//
// Risk covered: the watcher and the concurrency queue are fire-and-forget async
// (`void watchTick(run)`, `void drainQueue()`), which is exactly where a race or
// a lost timer silently strands a run. The shutdown test at the bottom pins the
// other half of that race: a tick in flight must not re-arm its timer after
// `session_shutdown` has cleared the timer map.

import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import * as path from "node:path";
import { test } from "node:test";

import subagentExtension, { type RunRecord } from "../index.ts";
import { waitFor, withEnv, withTempAgentDir } from "./helpers.ts";

const SESSION_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

interface Harness {
	pi: Record<string, unknown>;
	ctx: Record<string, unknown>;
	tools: Map<string, Record<string, unknown>>;
	execCalls: { command: string; args: string[] }[];
	messages: { message: Record<string, unknown>; options: Record<string, unknown> | undefined }[];
	shutdown: () => Promise<void>;
	call: (tool: string, params: Record<string, unknown>) => Promise<{ text: string; details: Record<string, unknown> }>;
	readRuns: () => Promise<RunRecord[]>;
	writeResult: (run: RunRecord, result: Record<string, unknown>) => Promise<void>;
}

interface HarnessOptions {
	maxConcurrent?: string;
	paneText?: string;
	paneDead?: boolean;
	/** Artificial delay (ms) added to every tmux call, so a shutdown can land while a watcher tick is mid-flight. */
	execDelayMs?: number;
}

async function createHarness(options: HarnessOptions = {}): Promise<Harness> {
	const tools = new Map<string, Record<string, unknown>>();
	const handlers = new Map<string, (...args: unknown[]) => unknown>();
	const execCalls: { command: string; args: string[] }[] = [];
	const messages: { message: Record<string, unknown>; options: Record<string, unknown> | undefined }[] = [];

	const pi = {
		registerFlag: () => undefined,
		getFlag: () => undefined,
		registerTool: (tool: Record<string, unknown>) => tools.set(tool.name as string, tool),
		registerCommand: () => undefined,
		registerMessageRenderer: () => undefined,
		registerEntryRenderer: () => undefined,
		on: (event: string, handler: (...args: unknown[]) => unknown) => {
			handlers.set(event, handler);
			return () => handlers.delete(event);
		},
		getThinkingLevel: () => "medium",
		sendMessage: (message: Record<string, unknown>, opts?: Record<string, unknown>) => {
			messages.push({ message, options: opts });
		},
		exec: async (command: string, args: string[]) => {
			execCalls.push({ command, args });
			if (options.execDelayMs) await new Promise((resolve) => setTimeout(resolve, options.execDelayMs));
			const joined = args.join(" ");
			if (joined.includes("-V")) return { code: 0, stdout: "tmux 3.3a", stderr: "" };
			if (joined.includes("capture-pane")) {
				return { code: 0, stdout: options.paneText ?? "child working\n", stderr: "" };
			}
			if (joined.includes("display-message")) {
				return { code: 0, stdout: options.paneDead ? "1" : "0", stderr: "" };
			}
			return { code: 0, stdout: "", stderr: "" };
		},
	};

	const ctx = {
		cwd: process.cwd(),
		mode: "tui",
		hasUI: true,
		isProjectTrusted: () => true,
		sessionManager: {
			getSessionId: () => SESSION_ID,
			getSessionFile: () => path.join(process.env.PI_CODING_AGENT_DIR ?? "/tmp", `${SESSION_ID}.jsonl`),
			getBranch: () => [],
		},
		model: { provider: "openrouter", id: "parent/model" },
		ui: {
			notify: () => undefined,
			custom: async () => undefined,
			setWidget: () => undefined,
		},
		shutdown: () => undefined,
	};

	await withEnv(
		{
			// The factory branches on these to decide whether it is running as a
			// child reporter or as the parent. They must be cleared from the
			// ambient environment: a reviewer subagent, or any pi subagent child,
			// already has them set, and without this the harness would silently
			// exercise the child branch and register no parent tools at all.
			PI_TMUX_SUBAGENT_CHILD: undefined,
			PI_TMUX_SUBAGENT_RESULT: undefined,
			PI_SUBAGENT_MAX_CONCURRENT: options.maxConcurrent ?? "4",
			PI_SUBAGENT_NOTIFY: "true",
			PI_SUBAGENT_AUTO_REAP: "true",
			PI_SUBAGENT_REAP_DELAY_MS: "0",
			PI_SUBAGENT_GC_DAYS: "7",
		},
		async () => {
			subagentExtension(pi as never);
			await handlers.get("session_start")?.({}, ctx);
		},
	);

	const runsIndex = path.join(process.env.PI_CODING_AGENT_DIR ?? "/tmp", "tmux-subagents", SESSION_ID, "runs.json");

	return {
		pi,
		ctx,
		tools,
		execCalls,
		messages,
		shutdown: async () => {
			await handlers.get("session_shutdown")?.({}, ctx);
		},
		call: async (tool, params) => {
			const definition = tools.get(tool);
			assert.ok(definition, `tool ${tool} must be registered`);
			const execute = definition.execute as (
				id: string,
				params: unknown,
				signal: undefined,
				onUpdate: undefined,
				ctx: unknown,
			) => Promise<{ content: { type: string; text: string }[]; details: Record<string, unknown> }>;
			const result = await execute(`call-${tool}`, params, undefined, undefined, ctx);
			return { text: result.content[0]?.text ?? "", details: result.details ?? {} };
		},
		readRuns: async () => JSON.parse(await readFile(runsIndex, "utf8")) as RunRecord[],
		writeResult: async (run, result) => {
			await writeFile(run.resultPath, `${JSON.stringify(result)}\n`, "utf8");
		},
	};
}

async function withHarness<T>(
	options: HarnessOptions | undefined,
	fn: (harness: Harness) => Promise<T>,
): Promise<T> {
	return withTempAgentDir(async () => {
		// withTempAgentDir already points PI_CODING_AGENT_DIR at a fresh temp
		// directory and restores the previous value afterwards.
		const harness = await createHarness(options);
		try {
			return await fn(harness);
		} finally {
			await harness.shutdown();
		}
	});
}

test("the harness exercises the parent branch even inside a pi subagent child", async () => {
	// This guards the harness, not the extension: createHarness clears
	// PI_TMUX_SUBAGENT_CHILD, which the extension factory branches on. Without
	// that, every test would silently register the child reporter instead of the
	// parent tools. Re-run the suite with PI_TMUX_SUBAGENT_CHILD=1 in the ambient
	// environment to check this holds.
	await withEnv(
		{ PI_TMUX_SUBAGENT_CHILD: "1", PI_TMUX_SUBAGENT_RESULT: "/tmp/should-not-be-used.jsonl" },
		async () => {
			await withTempAgentDir(async () => {
				const harness = await createHarness();
				try {
					assert.ok(harness.tools.has("subagent"), "parent tools are registered");
					assert.ok(harness.tools.has("subagent_status"), "parent tools are registered");
				} finally {
					await harness.shutdown();
				}
			});
		},
	);
});

test("subagent launches a child in a detached tmux session and returns immediately", async () => {
	await withHarness(undefined, async (h) => {
		const { text, details } = await h.call("subagent", { task: "Do the thing" });
		const run = details as unknown as RunRecord;

		assert.match(text, new RegExp(`Subagent ${run.id} started\\.`));
		assert.ok(text.includes("Model: openrouter/parent/model (medium)"));
		assert.ok(text.includes(`pi --attach-subagent '${run.id}'`));

		const launched = h.execCalls.some((call) => call.command === "tmux" && call.args.includes("new-session"));
		assert.ok(launched, "a tmux session is created");
		const keys = h.execCalls.filter((call) => call.args.includes("send-keys"));
		assert.ok(keys.length >= 2, "the child command is typed and submitted");
		assert.ok(
			keys.some((call) => call.args.some((arg) => arg.includes("--provider"))),
			"the child invocation carries the provider",
		);

		const runs = await h.readRuns();
		assert.equal(runs.length, 1);
		assert.equal(runs[0].status, "running");
		assert.equal(runs[0].task, "Do the thing");
	});
});

test("subagent rejects an empty task and a missing cwd", async () => {
	await withHarness(undefined, async (h) => {
		await assert.rejects(() => h.call("subagent", { task: "   " }), /must not be empty/);
		await assert.rejects(() => h.call("subagent", { task: "x", cwd: "/definitely/not/here" }), /does not exist/);
	});
});

test("runs beyond the concurrency cap are queued and start when a slot frees", async () => {
	await withHarness({ maxConcurrent: "1" }, async (h) => {
		const first = (await h.call("subagent", { task: "first" })).details as unknown as RunRecord;
		const second = (await h.call("subagent", { task: "second" })).details as unknown as RunRecord;

		const runs = await h.readRuns();
		assert.equal(runs.find((run) => run.id === first.id)?.status, "running");
		assert.equal(runs.find((run) => run.id === second.id)?.status, "queued");

		await h.call("subagent_cancel", { id: first.id });
		await waitFor(async () => (await h.readRuns()).find((run) => run.id === second.id)?.status === "running");
	});
});

test("a child result finalizes the run and notifies the main session", async () => {
	await withHarness(undefined, async (h) => {
		const run = (await h.call("subagent", { task: "long task" })).details as unknown as RunRecord;
		await h.writeResult(run, {
			version: 1,
			status: "completed",
			output: "the answer is 42",
			sessionFile: "/tmp/child.jsonl",
			provider: "openrouter",
			model: "child/model",
			thinking: "low",
			finishedAt: Date.now(),
		});

		await waitFor(async () => (await h.readRuns())[0].status === "completed");
		const finalized = (await h.readRuns())[0];
		assert.equal(finalized.output, "the answer is 42");
		assert.equal(finalized.model, "child/model");
		assert.ok(finalized.finishedAt);

		const notification = h.messages.at(-1);
		assert.equal(notification?.message.customType, "subagent-result");
		assert.deepEqual(notification?.options, { deliverAs: "followUp", triggerTurn: true });
		// The notification points at the run rather than inlining its output; the
		// agent collects the text with subagent_status.
		assert.ok(!String(notification?.message.content).includes("the answer is 42"));
		assert.ok(String(notification?.message.content).includes(finalized.id));
	});
});

test("a failed child result keeps the error visible", async () => {
	await withHarness(undefined, async (h) => {
		const run = (await h.call("subagent", { task: "will fail" })).details as unknown as RunRecord;
		await h.writeResult(run, {
			version: 1,
			status: "failed",
			output: "",
			error: "provider exploded",
			finishedAt: Date.now(),
		});

		await waitFor(async () => (await h.readRuns())[0].status === "failed");
		assert.match((await h.readRuns())[0].error ?? "", /provider exploded/);
		assert.ok(String(h.messages.at(-1)?.message.content).includes("provider exploded"));
	});
});

test("a dead pane without a result fails the run with capture guidance", async () => {
	await withHarness({ paneDead: true }, async (h) => {
		const run = (await h.call("subagent", { task: "dies early" })).details as unknown as RunRecord;
		await waitFor(async () => (await h.readRuns())[0].status === "failed");
		const failed = (await h.readRuns())[0];
		assert.match(failed.error ?? "", /exited before reporting a result/);
		assert.match(failed.error ?? "", /capture-pane/);
		assert.equal(failed.id, run.id);
	});
});

test("an unreadable result file does not finalize the run", async () => {
	await withHarness(undefined, async (h) => {
		await h.call("subagent", { task: "corrupt result" });
		const run = (await h.readRuns())[0];
		await writeFile(run.resultPath, "{not json", "utf8");
		await new Promise((resolve) => setTimeout(resolve, 1_500));
		assert.equal((await h.readRuns())[0].status, "running", "a parse failure must not be read as completion");
	});
});

test("subagent_cancel kills the tmux session and is idempotent once terminal", async () => {
	await withHarness(undefined, async (h) => {
		const run = (await h.call("subagent", { task: "cancel me" })).details as unknown as RunRecord;
		const killed = await h.call("subagent_cancel", { id: run.id });
		assert.match(killed.text, new RegExp(`Subagent ${run.id} cancelled`));
		assert.ok(
			h.execCalls.some((call) => call.args.includes("kill-session") && call.args.includes(run.tmuxSession)),
			"the tmux session is killed",
		);

		const again = await h.call("subagent_cancel", { id: run.id });
		assert.match(again.text, /already cancelled/);
		await assert.rejects(() => h.call("subagent_cancel", { id: "no-such-run" }), /Unknown subagent run/);
	});
});

test("subagent_status lists runs and inspects one", async () => {
	await withHarness(undefined, async (h) => {
		const empty = await h.call("subagent_status", {});
		assert.match(empty.text, /No subagent runs/);

		const run = (await h.call("subagent", { task: "inspect me" })).details as unknown as RunRecord;
		const listed = await h.call("subagent_status", {});
		assert.ok(listed.text.includes(run.id));
		assert.ok(listed.text.includes("inspect me"));

		const inspected = await h.call("subagent_status", { id: run.id });
		assert.ok(inspected.text.includes(`model: openrouter/parent/model (medium)`));
		await assert.rejects(() => h.call("subagent_status", { id: "nope" }), /Unknown subagent run/);
	});
});

test("subagent_wait blocks until the run finishes and returns its output", async () => {
	await withHarness(undefined, async (h) => {
		const run = (await h.call("subagent", { task: "wait for me" })).details as unknown as RunRecord;
		setTimeout(() => {
			void h.writeResult(run, {
				version: 1,
				status: "completed",
				output: "finished output",
				finishedAt: Date.now(),
			});
		}, 300);

		const waited = await h.call("subagent_wait", { ids: [run.id], timeout_seconds: 10 });
		assert.ok(waited.text.includes("finished output"));
	});
});

test("subagent_wait returns immediately when nothing is outstanding", async () => {
	await withHarness(undefined, async (h) => {
		const waited = await h.call("subagent_wait", {});
		assert.match(waited.text, /No incomplete subagent runs/);
		await assert.rejects(() => h.call("subagent_wait", { ids: ["ghost"] }), /Unknown subagent run/);
	});
});

test("subagent_wait aborts when the tool call is cancelled", async () => {
	await withHarness(undefined, async (h) => {
		await h.call("subagent", { task: "hangs forever" });
		const controller = new AbortController();
		const tool = h.tools.get("subagent_wait");
		const execute = tool?.execute as (...args: unknown[]) => Promise<unknown>;
		const promise = execute("call-1", {}, controller.signal, undefined, h.ctx);
		controller.abort();
		await assert.rejects(() => promise, /aborted/);
	});
});

test("subagent_clean reaps finished runs but leaves active ones alone", async () => {
	await withHarness(undefined, async (h) => {
		const done = (await h.call("subagent", { task: "finishes" })).details as unknown as RunRecord;
		const active = (await h.call("subagent", { task: "still going" })).details as unknown as RunRecord;
		await h.writeResult(done, { version: 1, status: "completed", output: "ok", finishedAt: Date.now() });
		await waitFor(async () => (await h.readRuns()).find((run) => run.id === done.id)?.status === "completed");

		const cleaned = await h.call("subagent_clean", {});
		assert.match(cleaned.text, /Cleaned 1 tmux session\(s\), deleted 0 run dir\(s\), skipped 1/);

		const stillThere = (await h.readRuns()).find((run) => run.id === active.id);
		assert.equal(stillThere?.status, "running");
	});
});

test("session_shutdown stops the watcher from polling tmux again", async () => {
	// Regression test for the post-shutdown timer leak (local patch 10).
	// A run that is still `running` has a live 500ms watcher. Shutting down while
	// a tick is in flight used to leave an orphan timer behind that nothing would
	// ever clear: the parent kept calling `pi.exec` (capture-pane) forever and kept
	// the event loop alive, so the suite needed `--test-force-exit`. The delay makes
	// the race deterministic: shutdown happens while the tick awaits `pi.exec`.
	// Assert on observable behaviour, not on timer internals.
	await withTempAgentDir(async () => {
		const harness = await createHarness({ execDelayMs: 400 });
		try {
			await harness.call("subagent", { task: "watched at shutdown" });
			// A capture-pane call is recorded before the fake applies the delay, so
			// this returns while that tick is still awaiting.
			await waitFor(async () => harness.execCalls.some((call) => call.args.includes("capture-pane")));

			await harness.shutdown();
			// The in-flight tick legitimately finishes its remaining calls after the
			// shutdown flag is set; what must not happen is a *new* poll.
			await new Promise((resolve) => setTimeout(resolve, 1_500));
			const callsAfterTick = harness.execCalls.length;
			await new Promise((resolve) => setTimeout(resolve, 2_000));

			const after = harness.execCalls.slice(callsAfterTick);
			assert.equal(after.length, 0, `no tmux calls after shutdown, got: ${JSON.stringify(after)}`);
			assert.ok(!after.some((call) => call.args.includes("capture-pane")), "the watcher must not poll the pane again");
		} finally {
			await harness.shutdown();
		}
	});
});

test("the shutdown flag is per extension load, not global", async () => {
	// The fix lives in the factory closure. If it leaked across loads, a session
	// that shut down would leave every later extension instance permanently
	// unable to watch a run. createHarness calls the factory again, so a fresh
	// instance must poll normally after the previous one was shut down.
	await withTempAgentDir(async () => {
		const first = await createHarness();
		try {
			await first.call("subagent", { task: "old instance" });
			await waitFor(async () => first.execCalls.some((call) => call.args.includes("capture-pane")));
			await first.shutdown();
		} finally {
			await first.shutdown();
		}

		const second = await createHarness();
		try {
			await second.call("subagent", { task: "new instance" });
			await waitFor(async () => second.execCalls.some((call) => call.args.includes("capture-pane")));
		} finally {
			await second.shutdown();
		}
	});
});

test("session_shutdown persists state without killing running children by default", async () => {
	await withHarness(undefined, async (h) => {
		const run = (await h.call("subagent", { task: "survives reload" })).details as unknown as RunRecord;
		h.execCalls.length = 0;
		await h.shutdown();
		assert.ok(!h.execCalls.some((call) => call.args.includes("kill-session")), "PI_SUBAGENT_KILL_ON_SHUTDOWN is off by default");
		assert.equal((await h.readRuns()).find((entry) => entry.id === run.id)?.status, "running");
	});
});