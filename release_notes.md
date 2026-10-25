---
id: C:/GIT/topomorph/apps/rag-master/release_notes.md
type: reference
title: rag-master change list
name: rag-master-change-list
description: ''
resource: /topomorph/apps/rag-master/release_notes.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master]
domain: tools
tokens: 10
generated: {by: 'human:brogers', at: '2026-08-15T22:45:11-04:00'}
okf_version: '0.2'
---
# Release Notes

## Changes

## 10-24

- Added `WEBAPP_REVIEW.md`: 27 suggested web app fixes and improvements (config escape round-trip bug, run/log reliability, Summary tab backend routing, Linux portability, live-log panel, schema-generated forms, status board, cleanup), each with a Codex review comment and a verdict for review.
- Removed the unused feeds table from the Source tab, which is now labelled "Run Settings": `datatables.js`, `util.js`, the table CSS and button images, the `/pipeline/` CSV proxies and `paths.json`. The run-settings form is unchanged.
- Removed the pywebview desktop runtime (`app-bridge.js`, `app-config.js`, and the pywebview branches in `index.html` and `source-init.js`). The app is HTTP-only now, and `index.html` loads tabs from a single registry (−400 lines).

## Future
