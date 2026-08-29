# Smart App Control実機証拠（0.3.2）

## 対象と照会条件

- OS: Smart App Control有効Windows 11
- package: `SkillMagnet.ContextMenu_0.3.2.0_x64__byy1sc3mfzfz4`
- 操作: Explorerで`C:\Projects\skill-magnet`を開き、folder backgroundの通常右クリックから`Skill Magnet` → `PMO`
- 成功条件: `Skill Magnet — 実行確認`が開く、native invoke logが`create_process_succeeded`を記録する、同じ検証時間帯にSkill Magnet関連Code Integrity 3033/3077がない

## 追跡可能な結果

2026-08-29 20:32 JSTの実操作では確認画面が開き、UTF-16 invoke logに`2026-08-29T11:32:06Z`の`invoke_enter`、`selection_succeeded`、`create_process_succeeded`（PID 7868）が記録された。ログにはcommand本文やproject pathを残さず、SHA-256 digestとevent、PID/error codeだけを保存する。

Code Integrityは`Microsoft-Windows-CodeIntegrity/Operational`を対象に、実操作後の時刻からevent ID 3033/3077を照会し、messageに`SkillMagnet`、`SkillMagnetCommand.dll`、`SkillMagnetIdentity.exe`、`SkillMagnetLauncher.exe`を含む件数を数えた。0.3.2実操作の検証時間帯は0件だった。0.3.0のWindows error 4551は別の失敗履歴であり、PASS証拠へ転用していない。

最終artifact-input commit `69d5029aaa2a4ae3338e664a7c4af524ae655f43`からcanonical wheelを再構築・再導入し、installed `SkillMagnetCommand.dll`のSHA-256が`b8f5975955c8ef6aa60ce02138248b45701615e11aac2282014bbfb61566a142`であることを確認した。2026-08-29 21:21:19 JSTに同じExplorer操作を再実行し、`Skill Magnet — 実行確認`を表示した。invoke logは`2026-08-29T12:21:19Z`の`invoke_enter`、`selection_succeeded`、`create_process_succeeded`（PID 24068）を記録した。同時刻以降の関連Code Integrity 3033/3077は0件だった。確認画面ではAI・依頼を入力せず、送信せずに終了し、PID 24068が残っていないことも確認した。
