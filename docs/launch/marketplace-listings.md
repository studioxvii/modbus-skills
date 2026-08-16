# Marketplace listings

Submit these yourself. The forms need the Studio Seventeen GitHub / Cursor /
Claude login. Do not claim a listing is live until the directory shows it.

Public repository: https://github.com/studioxvii/modbus-skills

Homepage: https://studioxvii.github.io/modbus-skills

## Shared listing text

**Name:** Modbus Skills

**Short description (one line):**

Read-only workflows that turn a vendor Modbus manual into a usable map, byte-order check, or polling-tool pack.

**Longer description:**

Modbus Skills helps controls, commissioning, and integration engineers turn a vendor PDF, spreadsheet, or register map into a human-readable user map plus JSON and CSV. It can compare firmware maps, show every supported byte-order layout from one sample, and build a disabled read-only Node-RED, Modpoll (BETA), or ModScan (BETA) pack.

The workflows do not generate write commands, broadcasts, discovery scans, or unlimited polling. When the documentation is unclear, they stop with a hold list instead of guessing.

Start with `$compile-user-map`. Use `$modbus-help` if the next step is unclear.

**Keywords:**

modbus, industrial-automation, node-red, register-map, byte-order, commissioning

**Category:** productivity / developer tools / industrial

**License:** Apache-2.0

**Author:** Studio Seventeen

## Click list

### 1. Cursor Marketplace

1. Open https://cursor.com/marketplace/publish
2. Sign in with the account that should own the listing
3. Submit `https://github.com/studioxvii/modbus-skills`
4. Paste the name, short description, and longer description above
5. Confirm the repo has `.cursor-plugin/plugin.json` or the generated Cursor package

### 2. Claude community marketplace

1. Validate locally if Claude Code is installed: `claude plugin validate ./plugins/modbus-skills`
2. Open https://platform.claude.com/plugins/submit or the claude.ai directory submission form
3. Submit the public GitHub URL
4. Paste the shared listing text
5. After approval, check https://github.com/anthropics/claude-plugins-community for the pin

### 3. cursor.directory

1. Open https://cursor.directory/plugins/new
2. Sign in with GitHub
3. Paste `https://github.com/studioxvii/modbus-skills`
4. Submit and wait for the safety scan

cursor.directory auto-detects `skills/*/SKILL.md` at specific paths. If the scan misses the plugin because skills live under `plugins/modbus-skills/skills/`, note that in the review and keep the GitHub README as the install source.

## After a listing is live

1. Add the live URL to the README install section
2. Replace “clone the repository” as the first Cursor / Claude path only when install is actually one click
3. Do not backfill old posts with a marketplace claim that was not true when they went out
