# Skill Magnet 0.5.4 リリース報告

## 判定

Skill CRUD実装はローカルリリースゲート合格。

## 主な変更

- Library Managerにpack→skill一覧を追加。
- ID入力なしのskill／pack更新・削除を追加。
- 依存中skill、空library、ID不一致、不正SKILL.mdをfail-closedで拒否。
- catalog管理ファイルだけをGitHub削除差分として許可。
- 同じGitHub保管庫の旧packを有効化時に除去。
- CLIへ`library list/update/delete`を追加。

## 証拠

- 全自動テスト: 168 PASS、環境依存1 skip
- Library Managerテスト: 26 PASS
- 実libraryコピーによるCRUDスモーク: PASS
- CI wheel論理payload SHA-256: `60d4aa77a48739a772ed3c58efad58a1fee838e15ee4fec10106743497fbff72`
- Windows package: `SkillMagnet.ContextMenu_0.5.4.0_x64__byy1sc3mfzfz4`
- Windows status: `menu_contract_matches_config=true`、`usable_installed_state=true`

## 成果物

- `dist/skill_magnet-0.5.4-py3-none-any.whl`
