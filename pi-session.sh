#!/usr/bin/env bash

model="${1:?Usage: $0 <model>}"

pi --provider openrouter --model "openrouter/$model"