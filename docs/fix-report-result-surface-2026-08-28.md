# Skill Magnet 完了結果surface 対応報告

日付: 2026-08-28  
対象: `C:\Projects\skill-magnet`  
状態: partial（実装・自動検証済み、実画面受入未実施）

## 変更

### Codex verification argv

Skill Magnetが生成するCodex verification argvへ次をprocess限定で追加した。

```text
-c mcp_servers.cloudflare-builds.enabled=false
-c mcp_servers.cloudflare-observability.enabled=false
-c mcp_servers.unreal-mcp.enabled=false
```

3件は `exec` より前に渡す。global/user MCP設定、Cloudflare/Unreal project、他processは変更しない。後続実機で`--ignore-user-config`とserver単位overrideの併用がtransport定義を消すことが判明したため同flagだけを除去し、`--ignore-rules`、read-only sandbox、approval never、output schemaを維持した。

### Session分離

verificationの `thread.started` / Claude session IDを利用者向けにresumeする処理を削除した。product sourceには `resume` commandとverification session IDのhandoffがない。`interactive_handoff=True` の成功時は、検証sessionを開かず `result_surface_ready`、`verification_session_resumed=false` を返す。

Codexアプリや別terminalを開く必要がないため、`codex://threads/new` も起動しない。利用者surfaceはSkill Magnet自身の結果windowであり、structured JSON履歴を持たない。

### Success surface

既定表示は日本語で次の5項目だけにした。

1. 完了
2. 実行したスキル
3. 依頼
4. 結果
5. 保存先/変更

verification statusと保存証拠pathは既定で閉じた「詳細」へ置く。raw JSON、evidence、contract ID、digest、hash、MCP/runtime診断は主表示に含めない。

structured resultへoptional `saved_paths` と `changes` を追加した。既存fieldはrequiredのままで、追加2 fieldを返さない既存runtime outputも受入可能である。申告がなければ推測せず、その旨を表示する。

### failed / blocked surface

起動失敗は `failed`、output/acceptance/cleanup/safety refusalは `blocked` として扱う。成功画面を表示せず、次を日本語で表示する。

- 原因
- 未実行・未確認の範囲
- 次の操作

内部exception本文、raw JSON、warning文字列は利用者surfaceへ転記しない。failure evidenceとterminal lifecycleは従来どおり保存する。

### CLI routing

Windows ExplorerとmacOS Finderの両context経路で、成功時はresult windowを表示してexit 0、失敗時はfailure windowを1回表示してexit 2とした。context commandは末尾のraw `json.dumps` / stderrへ到達しない。

## 維持した安全契約

- `verified_completed` 以外はsuccess surfaceを作れない。
- actual requestとSHA-256 bindingを維持。
- completed skill IDs/statusを維持。
- skillごとのapplied rule identityとacceptance assertionを維持。
- output/schema/cleanup failureは成功にしない。
- raw verification outputは保存証拠に残し、主表示だけから分離。

## Test

Focused:

```text
python -m unittest \
  tests.test_activation.ActivationEndToEndTest.test_codex_verification_uses_process_local_mcp_overrides \
  tests.test_activation.ActivationEndToEndTest.test_verification_session_is_not_resumed_and_surface_hides_raw_json \
  tests.test_activation.ActivationEndToEndTest.test_failed_and_blocked_surfaces_are_japanese_and_never_success \
  tests.test_activation.ActivationEndToEndTest.test_windows_context_collects_actual_request_before_execution \
  tests.test_activation.ActivationEndToEndTest.test_windows_context_failure_returns_without_console_output \
  tests.test_activation.ActivationEndToEndTest.test_completion_contract_rejects_each_mismatched_claim

Ran 6 tests in 4.285s
OK
```

Focused testは次を確認した。

- verification argvに3件のprocess-local overrideが正確に入り、`exec` より前にある。
- structured verification sessionをresumeせず、session IDも利用者handoffへ渡さない。
- success主表示にraw evidence/contract/digest/hash/thread eventがない。
- 保存先/変更の申告あり・なしを推測せず表示する。
- failed/blockedが日本語で原因・未確認範囲・次の操作を持ち、内部exception本文を表示しない。
- actual request、acceptance、fail-closed mismatch拒否を維持する。

Full suite:

```text
python -m unittest discover -s tests

Ran 103 tests in 97.291s
OK (skipped=1)
```

```text
python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

`git diff --check` は既存tracked fileのLF→CRLF warningだけを表示し、whitespace errorはなかった。global/user Codex config SHA-256はvalidation前後とも `ae98ac4db2b07b17511b92bdbc0beb1a0a9646293b0cb224e80af34c1afcff03` で不変だった。

## Installed source/bootstrap

installed menuの9 leavesはすべて `C:\Projects\skill-magnet\src` とcurrent `skill-magnet.json` を参照している。今回変更した `activation.py`、`ui.py`、`cli.py` は次回起動時にこのsource rootから直接loadされる。native/menu contract変更はないため、追加installは不要である。

Current source SHA-256:

- `activation.py`: `e39f164e7492989d6eae144942e4408bfec54bf373082001b4feee1d8b24c94c`
- `ui.py`: `a7b32979f16b4fae1ab695dd000bd31810c96664f90108d3a497b3971a1d161d`
- `cli.py`: `e4fb5f69abbf4ad67aee17d1727786115dac60e7af10ed933e9de9e240c1b4e6`

## 未完了

- Explorer/Tk実画面で5項目と閉じた詳細を確認する手動受入
- 実Codex processで3 MCP warningが表示されないことの実機確認
- 実依頼の `saved_paths` / `changes` 表示確認

Computer Use、GUI操作、実Codex/Claude起動は実施していない。実機証拠がないためcompletedとはしない。
