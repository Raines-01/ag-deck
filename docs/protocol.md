# Antigravity local RPC — notes

Reverse-engineered against **Antigravity 2.23.0**. Everything AG Deck does goes
through this endpoint; there is no screen scraping or UI automation.

## 1. Discovery

`resources/bin/language_server` is started by the Electron app as a child
process. Its command line looks like:

```
/Applications/Antigravity.app/Contents/Resources/bin/language_server
  --csrf_token 6f1c...            # random per start
  --app_data_dir /Users/me/Library/Application Support/Antigravity
  --override_ide_version 2.23.0
  ...
```

It listens on a **random loopback port**, and every request must carry the
token in the `x-codeium-csrf-token` header.

Discovery steps:

1. enumerate processes, keep the ones whose *executable* is `language_server`
   (`/proc` on Linux, `ps` on macOS, PowerShell CIM on Windows);
2. extract `--csrf_token` from the command line;
3. resolve the listening ports of that PID
   (`/proc/net/tcp` + fd inodes, `lsof`, `netstat -ano`);
4. probe `POST /exa.language_server_pb.LanguageServerService/GetAllCascadeTrajectories`
   over http and https until a response contains `trajectorySummaries`.

Token and port change on every restart, so the result is cached for 45 s and
rediscovered on any transport error.

Protocol: **Connect**, JSON encoding. A response error looks like
`{"code": "not_found", "message": "..."}` with HTTP 200; check the body.

```
POST http://127.0.0.1:<port>/exa.language_server_pb.LanguageServerService/<Method>
x-codeium-csrf-token: <token>
Content-Type: application/json
```

## 2. Methods used

| Method | Purpose | Request |
|---|---|---|
| `GetAllCascadeTrajectories` | session list | `{"excludeSubtrajectories": true}` |
| `GetCascadeTrajectorySteps` | one page of steps | `{"cascadeId": "...", "stepOffset": 0}` |
| `GetCascadeModelConfigData` | model list | `{"cascadeId": "..."}` |
| `StartCascade` | create an empty session | `{"workspaceUris": ["file:///path"], "source": "CORTEX_TRAJECTORY_SOURCE_CIDER"}` |
| `SendUserCascadeMessage` | send a message | see below |
| `CancelCascadeInvocation` | stop the current run | `{"cascadeId": "..."}` |
| `DeleteCascadeTrajectory` | delete a session | `{"cascadeId": "..."}` |

### Session list

```json
{
  "trajectorySummaries": {
    "<cascadeId>": {
      "stepCount": 9846,
      "status": "CASCADE_RUN_STATUS_IDLE",
      "createdTime": "2026-10-01T12:00:00Z",
      "lastModifiedTime": "2026-10-25T08:00:00Z",
      "annotations": {"title": "...", "archived": false},
      "workspaces": [{
        "workspaceFolderAbsoluteUri": "file:///home/me/project",
        "branchName": "main",
        "repository": {"computedName": "project"}
      }]
    }
  }
}
```

Statuses seen: `CASCADE_RUN_STATUS_IDLE`, `RUNNING`, `BUSY`, `CANCELING`.
AG Deck treats the three latter as "running", but also treats a finished
assistant reply as end-of-turn because the status flag lags a few seconds.

### Steps

`GetCascadeTrajectorySteps` returns a window starting at `stepOffset`
(`{"steps": [...]}`). There is no `limit`; the server decides the page size.
To read the tail, first read `stepCount` from the list, then request
`max(0, stepCount - N)` and fall back to offset 0 if the response is empty.

Each step:

```json
{
  "type": "CORTEX_STEP_TYPE_RUN_COMMAND",
  "status": "CORTEX_STEP_STATUS_DONE",
  "metadata": {
    "createdAt": "...", "startedAt": "...", "completedAt": "...",
    "modelUsage": {"model": "MODEL_PLACEHOLDER_M318", "inputTokens": 8200, "outputTokens": 310},
    "toolCall": {"name": "manage_task", "argumentsJson": "{\"Action\":\"...\"}"}
  },
  "runCommand": {"commandLine": "npm test", "cwd": "...", "exitCode": 0,
                 "combinedOutput": {"full": "..."}}
}
```

The payload lives under a type-specific key (`plannerResponse`, `viewFile`,
`codeAction`, `userInput`, …). Unknown types fall back to the first object
value. `CORTEX_STEP_TYPE_GENERIC` carries its real tool call in
`metadata.toolCall` (for example `manage_task`, which renders sub-agent tasks).

Notable payloads:

- `plannerResponse`: `response`, `modifiedResponse`, `thinking`, `toolCalls[]`, `stopReason`
- `userInput`: `items[].text`, `userResponse`, `userConfig.plannerConfig.planModel`
- `codeAction`: `actionResult.edit.absoluteUri`, `actionResult.edit.diff.unifiedDiff.lines[]`,
  `diffStats.{additions,deletions}`
- `viewFile`: `absolutePathUri`, `startLine`, `endLine`, `numLines`, `content`

## 3. Sending a message

```json
{
  "cascadeId": "...",
  "items": [{"text": "run the tests again"}],
  "cascadeConfig": {"plannerConfig": {"planModel": "MODEL_PLACEHOLDER_M318"}},
  "blocking": false
}
```

Two traps:

1. **`planModel` is required.** Without it the call returns 200 and then appends
   an error step: `failed to construct executor: plan model not specified`.
   Use the model of the previous turn, or `GetCascadeModelConfigData`.
2. **`StartCascade` only creates an empty session.** It does not execute the
   items, so a new session needs a second `SendUserCascadeMessage`.

Also filter the model list: disabled entries (for example
`gemini-4-argon-disabled`) are still returned and fail with
`NOT_FOUND: Requested entity was not found` if selected.

## 4. Compatibility

Method names, status enums and the `planModel` requirement are all version
specific. AG Deck keeps every call in `agdeck/rpc.py` and `agdeck/client.py`,
and reports failures through `agdeck doctor` instead of silently rendering an
empty UI, so a breaking change should be obvious rather than mysterious.
