# Skill Magnet 完了結果surface 根本原因調査

日付: 2026-08-28  
対象: `C:\Projects\skill-magnet`  
状態: 原因確定・source修正済み、実画面受入未実施

## 結論

raw JSONと無関係なMCP診断が利用者へ露出した直接原因は、verificationと利用者向けhandoffを同じCodex sessionとして扱っていたことである。旧 `ActivationEngine.execute` はstructured output検証後に `thread.started` のsession IDを取得し、Windows Terminalで `codex ... resume <verification-session-id>` を実行していた。これにより、出力schema専用の会話履歴とruntime診断を含むverification sessionそのものが利用者surfaceになった。

また、旧resume processのargvにはverification用の設定隔離が引き継がれなかった。global/user MCP設定を変更していなくても、再開したinteractive Codex processはuser configを読み、今回不要な `cloudflare-builds`、`cloudflare-observability`、`unreal-mcp` の起動診断を表示し得た。

成功結果を利用者向けに再構成する専用surfaceも存在しなかった。Windows context経路はverification sessionをresumeし、macOS context経路は成功結果dictをCLI末尾の `json.dumps` へ流していた。failed/blocked時は内部exception文字列を汎用errorへ埋め込み、原因、未実行・未確認範囲、次の操作を利用者向けに分離していなかった。

## 確認した旧処理連鎖

1. Codex verificationを `exec --json --output-schema --output-last-message` で実行する。
2. stdoutの `thread.started.thread_id` をverification session IDとして取得する。
3. `interactive_handoff=True` のとき `_launch_interactive_session` を呼ぶ。
4. Windows Terminal内で同じsessionを `resume` する。
5. 利用者は結果要約ではなく、verificationのstructured JSON履歴とruntime診断を含むsessionを見る。

この因果関係は旧 `src/skill_magnet/activation.py` の `_session_id`、`_launch_interactive_session`、`execute` の呼出しで確定した。修正後のproduct sourceにはquoted `resume` command、`session_id` handoff key、`interactive_ready`、Windows Terminal handoffは残っていない。

## Codex per-process overrideの確認

ローカル `codex-cli 0.148.0` の `codex --help` と `codex exec --help` は、`-c, --config <key=value>` をuser configへ対する正式なprocess単位overrideとして定義し、nested値にdotted pathを使用するよう明記している。

次のread-only確認で、各overrideが対象serverだけをdisabledとして解決することを確認した。

```text
codex -c 'mcp_servers.cloudflare-builds.enabled=false' mcp get cloudflare-builds
cloudflare-builds (disabled)

codex -c 'mcp_servers.cloudflare-observability.enabled=false' mcp get cloudflare-observability
cloudflare-observability (disabled)

codex -c 'mcp_servers.unreal-mcp.enabled=false' mcp get unreal-mcp
unreal-mcp (disabled)
```

global/user `C:\Users\HOMEA\.codex\config.toml` は削除・編集していない。validation windowで記録したSHA-256は `ae98ac4db2b07b17511b92bdbc0beb1a0a9646293b0cb224e80af34c1afcff03` である。

## 修正設計

### Process限定MCP無効化

`src/skill_magnet/activation.py:18-31` で3件の `mcp_servers.<name>.enabled=false` を定義し、Codex verification argvの `exec` より前へ反復 `-c` として追加した。後続の実機調査で、`--ignore-user-config`との併用はserver transport定義を消して`invalid transport`になることが判明したため、user configを読みつつprocess overrideで3件だけを無効化する。`--ignore-rules`、read-only sandbox、approval neverは維持する。他project/taskのprocessやglobal configへ変更を残さない。

### Verification sessionと結果surfaceの分離

structured JSONを生成したsession IDの抽出、resume、Windows Terminal起動、handoff process探索・cleanupコードをproduct sourceから削除した。`interactive_handoff=True` は新sessionを開かず、検証済みresult surfaceを表示可能にするだけである。返却状態は `result_surface_ready` と `verification_session_resumed=false` を明示する。

### 利用者向け結果

`src/skill_magnet/activation.py:727` はverification済みoutputから次だけを別objectへ構成する。

- 完了
- 実行したスキル
- 依頼
- 結果
- 保存先/変更

保存先と変更はstructured resultのoptional `saved_paths` / `changes` から作る。申告がなければ「保存先/変更の申告なし（結果のみ）」と表示し、存在しない変更を推測しない。これらは既存acceptance fieldのrequired条件を変更せず、`task_output`、actual request SHA-256、completed skill IDs/status、skill固有assertionを維持する。

`src/skill_magnet/ui.py:149` はraw output/evidenceを受け取らない主表示dictを作る。`src/skill_magnet/ui.py:260` の画面は日本語5項目だけを既定表示し、検証状態と保存証拠pathは閉じた「詳細」へ置く。raw JSON、contract、digest、hash、runtime診断は主表示へ渡さない。

### failed / blocked

typed failureを `_LaunchFailed`、`_OutputFailed`、`_AcceptanceFailed`、`_CleanupFailed`、その他の安全拒否に分け、日本語の原因、未実行・未確認範囲、次の操作へ写像する。exception本文やMCP warning文字列は表示へ転記しない。warning文字列のfilterではなく、成功判定前のtyped failureと保存証拠を維持したfail-closed処理である。

## なぜtestが検出しなかったか

既存testは同じsession IDを含むresume commandがWindows Terminalへ渡ることを成功条件としていた。つまりraw verification sessionの再開を不具合ではなく「visible handoff」として固定していた。MCP isolation testはなく、成功結果の主表示field、raw evidence非露出、failed/blockedの日本語構造もtest契約に存在しなかった。

## Installed source/bootstrap条件

installed `SkillMagnetMenu.tsv` はv3・9 leavesすべてで `C:\Projects\skill-magnet\src` を `sys.path.insert` し、`C:\Projects\skill-magnet\skill-magnet.json` を参照する。今回の変更はPython sourceだけで、native DLL、launcher、menu command contract、config、pack digestを変更していない。このinstalled bootstrap構造では次回起動からcurrent sourceが使われるため、追加install transactionは不要である。

これはsource参照の反映条件を満たすことだけを示す。Explorer/Tk実画面の5項目表示、details初期閉鎖、MCP warning非表示はComputer Use禁止のため未確認である。
