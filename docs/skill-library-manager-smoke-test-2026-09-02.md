---
artifact: smoke-test-report
created: 2026-09-02
target: Skill Library Manager
result: PASS
---

# Skill Library Manager スモークテスト結果

## 結論

Skill Library ManagerのCLI基本経路、実Windows GUIの通常1画面・最大2画面表示、Explorer右クリックからの起動をスモークし、すべてPASSした。外部repositoryへのpush、PR作成、本体config更新は不可逆または外部書込みを伴うため、本スモークでは確認画面までとした。これらのtransaction本体は自動E2EとWindows/macOS CIで検証済みである。

## 実行環境

| Item | Value |
|---|---|
| Date | 2026-09-02 |
| OS | Windows |
| Repository | `C:\Projects\skill-magnet` |
| Branch | `codex/readme-library-manager-guide` |
| Commit | release evidence台帳の`release_code_sha`を参照 |
| UI | Tk Skill Library Manager |

## CLIスモーク

一時directoryに対して次の製品commandを実行した。

1. `library init`
2. `library add`
3. `library validate`
4. `library status`

結果:

- repository名: `skill-magnet-skills`
- pack: `smoke-pack`
- skill: `smoke-skill`
- `INDEX.md`、catalog、`SKILL.md`、`acceptance.json`のSHA-256 manifest生成: PASS
- menu shape digest生成: PASS
- validation: `valid: true`
- 初期transaction一覧: 0件

## 実GUIスモーク

`python -m skill_magnet library ui`を実際に起動し、対象windowを一意に特定して次の表示を確認した。

| Step | Screen | Result |
|---:|---|---|
| 1 | 標準folderを選択した通常flowはPublishだけ | PASS |
| 2 | 作成済みskillの手動登録flowはSkillとPublish | PASS |

確認した安全境界:

- Publish画面に差分確認checkbox、`Publish PR`、`Verify merged remote`、`Skill Magnetへ反映`が表示される。
- platform選択を表示せず、Windows/macOSを実行環境から自動判定する。
- URL未入力、構成不備、validation失敗はエラーダイアログで停止する。
- smoke中にpublish、activate、menu更新は実行されていない。

## Explorer右クリック起動スモーク

現行sourceからWindows 11 modern context menuをbuild・再登録し、`C:\Projects\skill-magnet` folderを実際に右クリックして確認した。

| Check | Result |
|---|---|
| 通常右クリックに`Skill Magnet`が1入口だけ表示 | PASS |
| サブメニューに`Skill Library Manager`と`Delivery Assurance`の2 action | PASS |
| 標準folderからの`Skill Library Manager`選択でPublishだけが起動 | PASS |
| `Draft directory`、repository名、保存先の入力欄が表示されない | PASS |
| 作業用repositoryがアプリ専用state内に自動決定される | PASS |
| 右クリック対象は`SKILL.md`がある場合だけimport候補になる | PASS |
| 起動だけでrepository作成、publish、activateが行われない | PASS |

インストールstatusは`menu_contract_valid: true`、`menu_contract_matches_config: true`、`menu_leaf_count: 1`、`menu_action_count: 2`、`library_manager_entry_count: 1`、`usable_installed_state: true`だった。

## 後処理と再確認

- GUI windowを正常終了した。
- `pythonw.exe`の対象processが終了したことを確認した。
- 製品所有の一時smoke directoryを削除した。
- `tests.test_library_manager` 10件を再実行し、全件PASSした。
- repositoryは`main...origin/main`、未コミット変更なしの状態から文書更新を開始した。

## 判定

Skill Library Managerの右クリック起動、標準folderの自動import、library/catalog/INDEX自動準備、skill追加、Publish時のfail-closed検証、OS自動判定にリリース阻害問題はない。
