# Skill CRUD 実装報告

## 結果

Library Managerへpack→skillの階層一覧とCreate・Read・Update・Deleteを実装した。CRUD変更はローカル管理領域の隔離候補で検証後に反映され、既存のGitHub PR、merge検証、有効化、復旧フローへ接続される。

## 実装内容

- Read: packを親、skillを子とする一覧、表示名、種類、内部ID、説明、所属を表示。
- Create: フォルダーから単一skill、pack、複数packを登録。同一skill集合の重複packを拒否。
- Update: 一覧で選択したskill／packを同じIDのフォルダーから更新。ID不一致と不正構成を拒否。
- Delete: skill／packをcatalog、INDEX、管理ファイルから削除。依存中skillと最後のpack／skillを拒否。
- Atomicity: 全変更をアプリ管理領域のコピーへ適用し、完全validation後に入れ替える。失敗時は元のlibraryを保持。
- Publish: 以前のcatalogで管理されていたファイルだけ削除差分を許可し、READMEなど管理対象外ファイルの削除は従来どおり拒否。
- Activation: 同じGitHub repository URLの旧pack集合を新catalogで置換し、削除済み・旧名packをメニューへ残さない。別repositoryのpackは保持。
- Reconcile: GitHub差分0件でもremote manifestを検証し、古いconfig／menuだけを現catalogへ再同期できる。
- CLI: `library list`、`library update`、`library delete`を追加。
- GUI: タブを増やさず、一覧と`新規登録`、`選択項目を更新`、`選択項目を削除`、`再読込`を既存の単一画面へ追加。

## 失敗TREEの確認

| 起点 | 分岐 | 結果 |
|---|---|---|
| 更新 | 選択IDとフォルダーIDが違う | 変更前に拒否 |
| 更新 | SKILL.mdのtrigger／boundary不足 | 隔離候補のvalidationで拒否しrollback |
| 更新 | packからskillが消える | 他pack未使用なら管理ファイルを削除、共有中なら保持 |
| 削除 | 他skillからdepends-onされている | 依存元を表示して拒否 |
| 削除 | 最後のpack／skillになる | 公開不能な空libraryを作らず拒否 |
| 公開 | 削除対象が旧catalog管理ファイル | 明示CRUD差分として許可 |
| 公開 | README等の管理対象外ファイルが消える | 送信前に拒否 |
| 有効化 | 同じrepositoryの旧packがconfigに残る | 現catalog集合で置換して除去 |
| 有効化 | menu更新に失敗 | configを直前版へ戻す |

## 検証結果

- `python -m compileall -q src tests`: PASS
- `python -m unittest tests.test_library_manager -v`: 26件PASS
- `python -m unittest discover -s tests`: 168件PASS、環境依存1件skip
- 実ユーザーlibraryのコピー: 2 pack／21 skillのRead、skill Update、pack Delete、再validationがPASS
- 実ユーザーlibrary本体とGitHub repositoryはスモークで変更していない。
- Windows導入状態: `menu_contract_matches_config=true`、`usable_installed_state=true`
- GitHub差分0件の再同期transaction `ac27c80846834b6db61760cda8f364d1`を実行し、旧`Delivery Assurance`を除去。現行menuは2 pack＋Library Manager。
- 0.5.4 CI wheel論理payload SHA-256: `60d4aa77a48739a772ed3c58efad58a1fee838e15ee4fec10106743497fbff72`
- 実機へ0.5.4 wheelとWindows 11 modern context menuを再登録済み。

## 配布

この変更は0.5.4としてversionを更新する。0.5.3の履歴を上書きしない。
