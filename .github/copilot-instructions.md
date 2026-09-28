---
id: C:/GIT/harness-root/apps/rag-master/.github/copilot-instructions.md
type: reference
title: copilot-instructions
name: copilot-instructions
description: ''
resource: /harness-root/apps/rag-master/.github/copilot-instructions.md
resource_root: C:/GIT
author: brogers
tags: [harness-root, apps, rag-master, .github]
domain: tools
tokens: 113
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Alway on, global instructions

- Never run a terminal process in the background. If you need to run a process for a long time, use a tool like `tmux` or `screen` to manage it in the foreground.
- If a development phase is complete, suggest pushing to github to create a checkpoint. For example, after working in one directory, starting work on a different aspect of the code may be a good time to push to github.
- Never create a fallback. Code should fail verbosely if the implementation does not work.

