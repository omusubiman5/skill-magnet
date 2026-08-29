# Skill Magnet 完了形式エラーとCLI残留の原因調査（2026-08-28）

## 結論

最新実機失敗はAIのschema生成失敗ではない。Codex CLIがmodel呼出し前のconfig読込で終了したにもかかわらず、Skill Magnetがruntime終了を `_OutputFailed` として一括処理したため、「AIの出力が検証可能な完了形式を満たしませんでした」と誤表示した。

同時に、Explorerがconsole-subsystemの `python.exe` を直接起動していたため、Windows Terminalの `cmd.exe` tabと`conhost.exe`が生成された。エラーdialogを保持するPython processが終了するまでconsoleも残った。

## 最新attemptの同定

| 項目 | 実値 / 証拠 |
|---|---|
| contract ID | `003c4051138d4a909109950f1a15a307` |
| attempt ID | `9ee17d5de8334a388be29d3c3f0c6830` |
| confirmed / consumed | `2026-08-28T06:55:41.413108Z` / `2026-08-28T06:55:41.608247Z` |
| terminal evidence | `2026-08-28T15:55:41.7708276+09:00`, `output_failed` |
| Explorer child PID | `12552` (`python.exe`, start `2026-08-28 15:55:14`) |
| console child | PID `7684`, `conhost.exe`, parent PID `12552` |
| actual request | `うんこ` |
| actual-request SHA-256 | `6c90e96f0f18626586672eb733b2eb1346f67f4dcaf748ab1accbfbf628ac50c` |
| contract | `C:\Users\HOMEA\.skill-magnet\launch-contracts\003c4051138d4a909109950f1a15a307.json` |
| negative evidence | `C:\Users\HOMEA\.skill-magnet\evidence\003c4051138d4a909109950f1a15a307-not-guaranteed.json` |
| native invoke log | `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu\invoke.log`; `2026-08-28T06:55:14.683Z`, PID `12552` |

contract作成からnegative evidenceまで約0.36秒、runtime実行開始相当の`consumed_at`から約0.16秒であり、model応答を生成した時間はない。

## 最初の不一致

修正前の `src/skill_magnet/activation.py:828-832` は、process限定overrideとして `mcp_servers.cloudflare-builds.enabled=false` 等を渡す一方、同じargvに `--ignore-user-config` を渡した。後者がuser config内のserver transport定義を除外し、前者が`enabled`だけの不完全なserver tableを新設した。

同じCodex CLI 0.148.0に同じargv前半を渡したlocal再現の最初の実値は次のとおりで、exit codeは1だった。

```text
Error loading config.toml: invalid transport
in `mcp_servers.cloudflare-builds`
```

従って最初の不一致はoutput schemaではなく、Codex configの`mcp_servers.cloudflare-builds`にtransportが無いことである。`cloudflare-observability`と`unreal-mcp`も同じ構造だが、parserは最初の`cloudflare-builds`で停止した。

## 表示がschema errorになった理由

修正前の `src/skill_magnet/activation.py:881-885` はreturn codeが非0なら原因に関係なく `_OutputFailed` を送出した。`src/skill_magnet/ui.py:212-218` は `_OutputFailed` を完了形式不一致へ写像した。さらに失敗時cleanupが`output.json`、events JSONL、schema、process markerを削除し、negative evidenceには`output_failed`しか残さなかった。このため保存証拠だけではconfig起動失敗とschema mismatchを区別できなかった。

## CLI windowの生成経路

1. `src/skill_magnet/platforms.py:71-84` の旧 `_cli_prefix` が`python.exe`をmenu commandの先頭にした。
2. `native/windows-modern-context-menu/SkillMagnetCommand.cpp:327-331` の旧`CreateProcessW`は`CREATE_UNICODE_ENVIRONMENT`だけでconsoleを抑止しなかった。
3. Windowsはconsole-subsystem child用にWindows Terminalの`cmd.exe` tabと`conhost.exe`を作った。
4. Python→`codex.cmd`も `COMSPEC /d /c` を `creationflags=0` で起動しており、親consoleが無い場合には別consoleを作り得た。

## なぜtestが検出しなかったか

- process-local MCP testはfake Python adapterを使い、Codexのconfig parserを通さなかった。
- そのtestは`--ignore-user-config`の存在自体をassertし、矛盾したargvを回帰契約として固定した。
- runtime非0とJSON/schema不一致を同じ`output_failed`として期待した。
- `test_pythonw_entrypoint...`は実commandが`python.exe`のため常にskipし、console生成を検査しなかった。
- native contractはmenu列挙が無副作用であることだけを確認し、launcher childのconsole有無を検査しなかった。

## 修正方針

- user configのserver transport定義を読み、正式なper-process `-c mcp_servers.<name>.enabled=false` で3件だけを無効化する。global configは変更しない。
- runtime非0を `_RuntimeFailed` / `runtime_failed` とし、JSON/schema不一致の `_OutputFailed` と分離する。成功判定、schema、acceptanceは緩和しない。
- Windows menuはGUI-subsystemのinstalled launcherを経由し、launcherとmodern COM serverの双方が`CREATE_NO_WINDOW`で製品所有childだけを起動する。
- Python→Codex/Claudeにも`subprocess.CREATE_NO_WINDOW`を渡す。Tk/result/error dialogは通常のGUI windowなので抑止しない。

