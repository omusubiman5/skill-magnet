# Skill Magnet 0.3.2 Smart App Control remediation report

## 判定

Smart App Control 4551修正componentはPASS。この判定は旧PMOメニューでnative DLLから確認UIを起動できたことだけを対象とし、現行Delivery Assurance packのDesktop自然文結果、Skill Magnet製品全体の完成、Microsoft Store等の公開配布を含めない。製品全体の現行判定は[`product-completion-remediation-report-2026-08-30.md`](product-completion-remediation-report-2026-08-30.md)のNO-GOである。

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
| unit/integration | PASS: 125 tests、1 environment-dependent skip |
| full MSIX status | PASS: `SkillMagnet.ContextMenu_0.3.2.0_x64`、package contentは`Program Files\\WindowsApps` |
| Explorer real UI | PASS: 通常右クリック → `Skill Magnet` → `PMO` → `Skill Magnet — 実行確認` |
| process launch | PASS: 最終DLLで`create_process_succeeded`、PID 24068 |
| Code Integrity after 0.3.2 invocation | PASS: Skill Magnet関連3033/3077は0件 |
| candidate artifact-input commit | PASS: `69d5029aaa2a4ae3338e664a7c4af524ae655f43` |
| canonical wheel | PASS: physical SHA-256 `e86ad84ef4d82235db5358bae952f9668a72ee4f70a9c3d747058af06b38b32c`、logical payload SHA-256 `9dd17c9ec1e7b4fa97fcd74fd55b34c46e2fc2a698f590eabca4cf21c5f17d40` |
| real OS lifecycle | PASS: install → update → rollback → uninstall |
| lifecycle residue | PASS: Appx、owned registry、rollback point、external test rootは0 |
| remote CI | PASS: final artifact-input commitと125 testsを含むrun `33252451350`でWindows/macOS green |

## 旧GO判定の訂正

0.3.0をGOとした記録は、この実機反証により撤回する。package登録、menu表示、status、CI lifecycleは「Explorerがnative codeをSmart App Control下でloadし、commandを起動できる」ことを証明していなかった。今回から実機Code Integrity logと`create_process_succeeded`を必須証拠に追加した。

## 最終鬼レビュー

初回鬼レビューの既存Appx破壊、実機未導入、root生成物、README/full MSIX矛盾、証拠不足を修正した。再監査で残った最終DLLのExplorer実操作も、21:21:19 JSTの確認画面表示、`create_process_succeeded`、関連Code Integrity 0件で解消した。lifecycle preflightはsource文字列検査に加え、既存rootとmarkerを作り、変更前に拒否してmarkerを保存するbehavior testを追加した。

P0/P1の未解決事項はない。
