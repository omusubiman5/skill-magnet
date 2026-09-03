# Skill CRUD 実行方針

## 実装順序

1. catalogからGUI用階層を返すRead APIを追加する。
2. 全変更を隔離候補で検証してから置換する共通transactionを追加する。
3. skill更新、pack更新、skill削除、pack削除APIを追加する。
4. Createへskill集合による意味的な重複検出を追加する。
5. 有効化を同一GitHub保管庫単位の置換へ修正する。
6. Library Managerへ一覧、詳細、CRUDボタンを追加する。
7. READMEを実際の操作と制約に合わせて更新する。

## 検証

- Create: 単一skill、pack、複数pack、同内容再選択、別ID同一集合。
- Read: pack→skill階層、表示名、説明、所属。
- Update: skill正常更新、ID不一致、pack構成変更、validation失敗時rollback。
- Delete: skill正常削除、依存中skill拒否、pack削除、最後のpack拒否。
- Activate: 同一repositoryの削除済みpackを除去し、別repositoryのpackを保持。
- 回帰: 全自動テスト。
- スモーク: 実ユーザーstateのコピーで一覧読込とCRUD一巡を行い、実stateとGitHubは変更しない。

## 完了条件

- 上記テストが成功する。
- GUIからID入力なしでCRUDを開始できる。
- ローカル変更、GitHub公開、マージ後有効化の状態遷移が一貫する。
- 未解決の重大事項があればリリースOKとはしない。

