# helper tool to update '.md file format TOC of any SW Detailed Design
import re

def generate_toc(file_path):
    """Generate a Markdown table of contents from headings in a Markdown file."""
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.readlines()
    
    toc = []
    for line in content:
        # Match Markdown headings (from # to ###)
        match = re.match(r'^(#{1,6})\s+(.*)', line)
        if match:
            level = len(match.group(1))  # Number of '#' symbols indicates heading level
            title = match.group(2).strip()
            # Create a URL-friendly anchor (lowercase, spaces to hyphens, remove special chars)
            anchor = re.sub(r'[^\w\s-]', '', title).replace(' ', '-').lower()
            toc.append(f"{'  ' * (level - 1)}- [{title}](#{anchor})")
    
    # Join all TOC lines with newlines
    toc_md = "\n".join(toc)
    
    # Print the generated Table of Contents
    print("# Table of Contents\n" + toc_md)

# Usage, adapt target '.md filename and path as needed
generate_toc("CompSN_DD.md")
