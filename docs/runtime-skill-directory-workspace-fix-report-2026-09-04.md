# ランタイムskill領域とtask workspaceの分離 修正対応報告

## 対応結果

`C:/Users/HOMEA/.codex/skills`をSkill Magnetの`対象プロジェクト`としてCodex Desktopへ渡した問題を修正した。Skill Magnetはこのpathをskillのインストール先、一時領域、成果物出力先、task workspaceのいずれとしても扱わない。さらに、別フォルダーからの再実行を要求していた初回修正を撤回し、projectless新規タスクへ自動変換して処理を継続するよう変更した。

原因と失敗TREEは[ランタイムskill領域を対象プロジェクトとして渡した原因調査](runtime-skill-directory-workspace-root-cause-2026-09-04.md)に記録した。

## 実装内容

1. `activation.validate_task_workspace()`で、次のruntime-managed skill rootと全子pathをtask workspaceの`None`へ正規化するようにした。
   - `~/.codex/skills`
   - `~/.agents/skills`
   - `~/.claude/skills`
2. 右クリック選択の事前検証とactivation planの両方で同じ正規化を行い、projectless launch contractとhandoffへ到達させる。
3. UI、確認dialog、Desktop promptを`プロジェクト`／`対象プロジェクト`から`作業対象フォルダー`へ変更した。
4. verified runtime envelopeの外部fieldを`TARGET_PROJECT`から`TASK_WORKSPACE`へ変更した。
5. Codex deep linkの`path`とClaude deep linkの`folder`をprojectless時だけ省略し、利用者の復旧操作なしで新規タスクを開く。
6. `policy/product-policy.json`へtask workspaceの目的、禁止root、非install、非temporaryを機械可読な不変条件として追加した。
7. READMEへGitHub source、task workspace、runtime skill rootの違いを追記した。
8. 初回実導入後に判明した「エラー内容は具体的でも利用者が復旧を負担する」欠陥を修正した。reserved skill pathは失敗ではなく、自動projectless遷移として扱う。

## 回帰試験

| ケース | 結果 |
|---|---|
| 通常の作業folder | PASS: 受理 |
| `~/.codex/skills` | PASS: projectlessへ自動変換 |
| `~/.codex/skills/cma-004` | PASS: projectlessへ自動変換 |
| `~/.agents/skills`、`~/.claude/skills` | PASS: 共通root定義とpolicy testで固定 |
| 自動変換後のlaunch contract | PASS: `project: null`で作成 |
| Codex deep link | PASS: `path`なし、promptあり |
| Claude deep link | PASS: `folder`なし、promptあり |
| skill領域への書込み | PASS: 0件 |
| 利用者の復旧操作 | PASS: 不要 |
| Desktop promptの旧`対象プロジェクト`表示 | PASS: 残留なし |
| 全自動suite | PASS: 187 tests、環境依存1 skip |
| release ledger consistency gate | PASS |

## buildと導入

- release code: `6d1e2b26662f15512ac41181628fba9b954efb2d`
- 独立build A wheel logical payload SHA-256: `fc59227fcf42ade3af3d10abe0162eef3dbce7e66b36f2f69edc0c43ef15d328`
- 独立build B wheel logical payload SHA-256: `fc59227fcf42ade3af3d10abe0162eef3dbce7e66b36f2f69edc0c43ef15d328`
- 同一payload判定: PASS
- 候補wheelを既存Python環境へ`--force-reinstall --no-deps`で導入: PASS
- import元: `C:\Users\HOMEA\AppData\Local\Programs\Python\Python312\Lib\site-packages\skill_magnet`

## 実Windowsスモーク

導入済みwheelをrepository外からimportし、実在する`C:\Users\HOMEA\.codex\skills\cma-004`を`custom-skills/cma-004`の右クリック選択元として検証した。Desktop protocolを実際に開く直前でdeliveryを捕捉し、不要なテストtaskを作らず最終handoff入力まで確認した。

- 結果: `desktop_handoff_ready`
- launch contract: `project: null`
- Codex deep link: `prompt`あり、`path`なし
- Claude deep link: `folder`なし
- skill領域: 全file SHA-256不変
- 利用者の復旧操作: 0回
- import元: `C:\Users\HOMEA\AppData\Local\Programs\Python\Python312\Lib\site-packages\skill_magnet`
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

## 初回修正後の再発と是正

初回候補ではworkspace拒否を正解と誤認していた。汎用エラーを具体的エラーへ直しても、利用者へ別フォルダーの選択を要求するため製品要件を満たさない。今回の修正では失敗表示の改善ではなく、reserved pathをprojectlessへ自動遷移させ、handoff成功までを検査対象にした。
