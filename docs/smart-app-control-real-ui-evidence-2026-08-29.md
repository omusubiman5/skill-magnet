# Smart App Control実機証拠（0.3.2）

## 対象と照会条件

- OS: Smart App Control有効Windows 11
- package: `SkillMagnet.ContextMenu_0.3.2.0_x64__byy1sc3mfzfz4`
- 操作: Explorerで`C:\Projects\skill-magnet`を開き、folder backgroundの通常右クリックから`Skill Magnet` → `PMO`
- 成功条件: `Skill Magnet — 実行確認`が開く、native invoke logが`create_process_succeeded`を記録する、同じ検証時間帯にSkill Magnet関連Code Integrity 3033/3077がない

## 追跡可能な結果

2026-08-29 20:32 JSTの実操作では確認画面が開き、UTF-16 invoke logに`2026-08-29T11:32:06Z`の`invoke_enter`、`selection_succeeded`、`create_process_succeeded`（PID 7868）が記録された。ログにはcommand本文やproject pathを残さず、SHA-256 digestとevent、PID/error codeだけを保存する。

Code Integrityは`Microsoft-Windows-CodeIntegrity/Operational`を対象に、実操作後の時刻からevent ID 3033/3077を照会し、messageに`SkillMagnet`、`SkillMagnetCommand.dll`、`SkillMagnetIdentity.exe`、`SkillMagnetLauncher.exe`を含む件数を数えた。0.3.2実操作の検証時間帯は0件だった。0.3.0のWindows error 4551は別の失敗履歴であり、PASS証拠へ転用していない。

最終artifact-input commitからcanonical wheelを再構築・再導入した後にも、同じ操作と照会を再実行して結果を本書へ追記する。
