# ランタイムskill領域とtask workspaceの分離 修正対応報告

## 対応結果

`C:/Users/HOMEA/.codex/skills`をSkill Magnetの`対象プロジェクト`としてCodex Desktopへ渡した問題を修正した。Skill Magnetはこのpathをskillのインストール先、一時領域、成果物出力先、task workspaceのいずれとしても扱わない。

原因と失敗TREEは[ランタイムskill領域を対象プロジェクトとして渡した原因調査](runtime-skill-directory-workspace-root-cause-2026-09-04.md)に記録した。

## 実装内容

1. `activation.validate_task_workspace()`を追加し、次のruntime-managed skill rootと全子pathをcontract作成前に拒否するようにした。
   - `~/.codex/skills`
   - `~/.agents/skills`
   - `~/.claude/skills`
2. 右クリック選択の事前検証とactivation planの両方で同じ検査を実行する。確認画面、launch contract、handoffまで到達させない。
3. UI、確認dialog、Desktop promptを`プロジェクト`／`対象プロジェクト`から`作業対象フォルダー`へ変更した。
4. verified runtime envelopeの外部fieldを`TARGET_PROJECT`から`TASK_WORKSPACE`へ変更した。
5. 拒否画面に、成果物を置くfolderを右クリックして再実行する復旧方法と、拒否された起動ではskillのinstall/copyを行っていないことを表示した。
6. `policy/product-policy.json`へtask workspaceの目的、禁止root、非install、非temporaryを機械可読な不変条件として追加した。
7. READMEへGitHub source、task workspace、runtime skill rootの違いを追記した。

## 回帰試験

| ケース | 結果 |
|---|---|
| 通常の作業folder | PASS: 受理 |
| `~/.codex/skills` | PASS: 確認前に拒否 |
| `~/.codex/skills/cma-004` | PASS: 確認前に拒否 |
| `~/.agents/skills`、`~/.claude/skills` | PASS: 共通root定義とpolicy testで固定 |
| 拒否後のlaunch contract | PASS: 未作成 |
| 拒否後のevidence | PASS: 未作成 |
| 日本語復旧表示 | PASS |
| 英語復旧表示 | PASS |
| Desktop promptの旧`対象プロジェクト`表示 | PASS: 残留なし |
| 全自動suite | PASS: 185 tests、環境依存1 skip |
| release ledger consistency gate | PASS |

## buildと導入

- release code: `12b4250c25c7c7bb44d5f639faafbc653685f1eb`
- 独立build A wheel logical payload SHA-256: `8eefa9641c1fb9a631f4da7fc41219bd91977f3b12de39ae04b641e5e19ea0bf`
- 独立build B wheel logical payload SHA-256: `8eefa9641c1fb9a631f4da7fc41219bd91977f3b12de39ae04b641e5e19ea0bf`
- 同一payload判定: PASS
- 候補wheelを既存Python環境へ`--force-reinstall --no-deps`で導入: PASS
- import元: `C:\Users\HOMEA\AppData\Local\Programs\Python\Python312\Lib\site-packages\skill_magnet`

## 実Windowsスモーク

導入済みwheelから、実在する`C:\Users\HOMEA\.codex\skills`を`custom-skills/cma-004`の作業対象として事前検証した。

- 結果: `rejected_before_confirmation`
- launch contract: 未作成
- evidence: 未作成
- 通常folder `C:\Projects\skill-magnet`: 受理
- Windows modern context menu: `installed: true`
- `menu_contract_matches_config: true`
- `usable_installed_state: true`
- menu leaf: 3
- menu action: 5

## 影響境界

- Skill Library Managerが登録元の`SKILL.md`を読み、製品所有の隔離authoring workspaceでGitHub公開差分を作る処理は維持した。
- 既存の`.codex/skills`内fileは変更・削除していない。
- skill contentの正本は引き続きユーザー所有GitHub repositoryである。
- 作業対象フォルダーは依頼と成果物のcontextであり、skill contentの保存・install・一時処理には使わない。
