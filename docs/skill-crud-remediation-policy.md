# Skill CRUD 修正方針

## 基本方針

Library Managerの内部正本を`skill-magnet.catalog.json`とし、画面操作は必ずcatalog、生成`INDEX.md`、skillフォルダーを一括更新する。利用者にはJSONを編集させない。

## データ整合性

- Readはcatalogを読み、packを親、skillを子とする階層データを返す。
- Createは既存の自動検出を使うが、pack IDだけでなくskill集合の完全一致も重複判定に使う。
- Updateは選択したIDと入力フォルダーから検出したIDの一致を必須にする。
- skill更新は、そのskillを共有する全packの表示メタデータも更新する。
- pack更新は選択したpackの構成、関係、説明、所属skillを置換する。別packが共有していない旧skillだけを削除する。
- Delete skillは全所属packから対象を除き、対象を参照する`depends-on`があれば拒否する。
- Delete packは対象packを除き、他packから使われないskillフォルダーだけを除く。
- 各変更は隔離候補上でvalidationしてから入れ替える。失敗時は現行libraryを保持する。

## GitHubと有効化

- CRUDはローカル管理領域だけを変更し、既存の確認→専用branch→PR→merge検証→有効化を再利用する。
- 有効化時はpack IDだけでなく、同じ`repo_url`に属する旧packを置換対象にする。これによりGitHub正本から削除されたpackをメニューから除去する。
- 別GitHub保管庫のpackは保持する。
- PR作成後のローカル破棄禁止、再試行、マージ待ちの扱いは既存の復旧契約を維持する。

## UI方針

- タブは増やさない。
- 画面上部に登録済みpack／skill一覧と詳細を表示する。
- 操作は「新規登録」「選択項目を更新」「選択項目を削除」「再読込」に限定する。
- 更新と削除は対象を一覧で選んでから実行する。内部IDの入力欄は設けない。
- GitHub送信は従来どおり、その時点で可能な次の操作だけを1ボタンで表示する。

