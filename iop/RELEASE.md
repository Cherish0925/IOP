# IOP 0.8.7

Continuous experiments can now be followed through a bounded update feed. It includes completion of operations already seen while queued, without retransmitting measurement arrays. Output files have a separate incremental page. REST, Python, TypeScript and MCP expose the same behavior.

Global event cursors no longer collide with stream chunk numbers or episode transition numbers. Older stored records are read correctly without rewriting laboratory data.

IOP Connect 1.2 adds portable Agent Plugins packages and DeepSeek Cordis/bundle configuration for the selected lab. MCP clients can read the operating guide on demand. Existing Codex, Claude Code, Research OS, Qiushi and stdio connections remain available.

Runtime installers retain the existing laboratory directory and driver configuration. Finish accepted work before replacing a running service.
