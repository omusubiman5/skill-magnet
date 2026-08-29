# Skill Magnet 0.3.2 Smart App Control remediation report

## 判定

ローカル実装とWindows実機受入はPASS。candidate commitと同一SHAのremote Windows/macOS CI完了までは最終GOを保留する。

## 原因

0.3.0のsparse packageは、Explorer COM DLLとprocess adapterを`%LOCALAPPDATA%`の外部locationへ置いていた。ExplorerはCOM DLLを列挙できる場合があったためmenu表示とstatusは成功したが、実行時の自己署名`SkillMagnetLauncher.exe`がSmart App Control policy ID `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`に拒否され、`CreateProcessW`が4551で失敗した。

launcherを外した初期0.3.1でも、外部locationの自己署名`SkillMagnetCommand.dll`がCode Integrity 3033/3077で拒否された。したがって「自己署名certificateをTrustedPeopleへ入れれば外部native codeも実行可能」という前提が誤りだった。

## 修正

- 0.3.2へversion同期した。
- `SkillMagnetLauncher.exe`とsourceを削除し、menu commandはMicrosoft/PSF chainでAuthenticode `Valid`のPythonを直接指定した。
- sparse packageの`AllowExternalContent`を廃止し、COM DLL、identity anchor、assets、固定menu manifestをfull MSIXへ収容した。
- `SkillMagnetIdentity.exe`はpackage identity anchor専用で、menu commandとして実行しない。
- statusは実package install locationを読み、DLL/identity/menu、config一致、Python署名、旧launcher不在を検査する。
- modern登録失敗時のclassic fallbackを廃止し、直前状態へrollbackして失敗を返す。
- native object/import library/export fileはnative out directoryだけへ生成する。

## ローカル証拠

| Gate | Result |
|---|---|
| native build / IExplorerCommand contract | PASS |
| unit/integration | PASS: 122 tests、1 environment-dependent skip |
| full MSIX status | PASS: `SkillMagnet.ContextMenu_0.3.2.0_x64`、package contentは`Program Files\\WindowsApps` |
| Explorer real UI | PASS: 通常右クリック → `Skill Magnet` → `PMO` → `Skill Magnet — 実行確認` |
| process launch | PASS: `create_process_succeeded`、PID 7868 |
| Code Integrity after 0.3.2 invocation | PASS: Skill Magnet関連3033/3077は0件 |
| candidate commit | PASS: `74774bc48b43ccf08360d4b89e204069e08ae193` |
| canonical wheel | PASS: logical payload SHA-256 `d2e822e490462ec55c19ca78ea0821448007ccf80cd3a549911e25261fac748d` |
| real OS lifecycle | PASS: install → update → rollback → uninstall |
| lifecycle residue | PASS: Appx、owned registry、rollback point、external test rootは0 |

## 旧GO判定の訂正

0.3.0をGOとした記録は、この実機反証により撤回する。package登録、menu表示、status、CI lifecycleは「Explorerがnative codeをSmart App Control下でloadし、commandを起動できる」ことを証明していなかった。今回から実機Code Integrity logと`create_process_succeeded`を必須証拠に追加した。

## 未完了ゲート

- candidateと同一SHAのWindows/macOS CI
- 最終独立レビュー

これらが完了するまで本報告は最終GOを宣言しない。
