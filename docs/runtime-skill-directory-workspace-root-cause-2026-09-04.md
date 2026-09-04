# ランタイムskill領域を対象プロジェクトとして渡した原因調査

## 再調査による訂正

初回方針の「runtime skill領域ならエラーで停止し、別フォルダーからやり直してもらう」は撤回する。右クリック対象がskillフォルダーであること自体は、利用者がそのskillを選んだ正当な入力である。誤りは、そのpathをtask workspaceとして渡したことであり、入力操作ではない。したがって必要な結果は拒否ではなく、task workspaceを`None`へ正規化し、デスクトップアプリのprojectless新規タスクへ自動handoffすることである。

初回修正は「危険なworkspaceを使わない」ことだけを完了条件にし、「利用者の依頼が操作追加なしで継続する」ことを完了条件に含めていなかった。このため、原因を説明するエラーへ改善しても、利用者が復旧操作を負担する欠陥が残った。

## 対象

- 事象: Skill Magnetが`C:/Users/HOMEA/.codex/skills`を`対象プロジェクト`としてCodex Desktopへ渡した。
- 関連契約: `94f24f9eb0a044b8a2c77f724dcdd480`
- 関連attempt: `f3adaac7b1cb4ee0830c829265a7f433`

## 確認済み事実

1. launch contractの`project`は`C:\Users\HOMEA\.codex\skills`だった。
2. desktop handoff evidenceの`skill_content_storage`は`github_only`だった。
3. 規範ポリシー`policy/product-policy.json`は、skillの永続正本をユーザー所有GitHub repositoryだけとし、persistent installとruntime materializationを禁止している。
4. activationの`plan()`は、右クリックで受け取ったpathについて「存在するdirectoryか」だけを検査していた。
5. `.codex/skills`、`.agents/skills`、`.claude/skills`とその配下をtask workspaceから除外する検査は存在しなかった。
6. UIとDesktop promptは受け取ったpathを`プロジェクト`／`対象プロジェクト`と表示し、skill保管先、作業場所、出力先の違いを説明していなかった。
7. Skill Magnet本体のactivation経路には、`.codex/skills`へ`SKILL.md`をコピーする処理はない。Library Managerの一時コピーは製品所有の隔離workspaceに限定され、GitHub反映後にcleanupする別処理である。

## 原因TREE

```text
利用者に「Skill Magnetがskillをインストールする」と誤認させた
├─ UI／promptが右クリックpathを「対象プロジェクト」と表示した
│  └─ task workspaceという責務名が契約外面に存在しなかった
├─ C:/Users/HOMEA/.codex/skillsを有効なworkspaceとして受理した
│  └─ plan()のpath検査が「存在するdirectory」だけだった
│     └─ runtime skill rootを禁止する不変条件がコード化されていなかった
└─ 一件のevidenceから製品全体のinstall仕様を説明した
   └─ 実行事実、規範ポリシー、全write経路を回答前に分離しなかった
```

## 根本原因

根本原因は、GitHub skill source、task workspace、成果物出力先、一時authoring workspace、runtime skill rootを別の責務として定義していたにもかかわらず、activation入力境界でその分離を強制していなかったことである。右クリックpathを無条件に`project`へ流す実装により、runtime skill rootがtask workspaceとして契約化された。

説明上の直接原因は、`github_only`という今回のhandoff証跡を「製品の全経路でinstall機能が存在しない」という証明に拡張したことである。規範ポリシーはpersistent installを禁止しているが、その根拠を確認する前に結論だけを回答したため、先行する「インストール先」という誤説明と矛盾した。

## 実導入後に判明したエラー表示の欠陥

初回修正後、2026-09-04 09:42の実右クリックではnative extensionのselectionとchild process作成は成功した。その後、workspace gateが`.codex/skills`を意図どおり拒否したが、CLIは具体的な`SkillMagnetError`をすべて`context_failure_message()`へ渡した。この関数のdefault分岐が例外内容を捨て、「安全確認または起動前検証を満たせませんでした」という汎用文へ置き換えた。

初回スモークは`context_error_message()`を直接検査しており、実右クリックCLIが使用する`context_failure_message()`を通していなかった。このため、内部の境界判定は検証できた一方、利用者が実際に見るsurfaceは未検証のままPASSと誤判定した。

追加の根本原因は、同じ例外に対する2つの表示変換と、実CLI経路を通らないスモークである。文字列一致ではなく型付きworkspace failureを導入し、CLIが表示するsurfaceまで回帰試験する必要がある。

## 失敗TREE

| 入力・状態 | 修正前の結果 | 必要な結果 |
|---|---|---|
| 通常の作業folder | workspaceとして受理 | 受理 |
| `~/.codex/skills` | workspaceとして受理 | projectlessへ自動変換してhandoff |
| `~/.codex/skills/<skill-id>` | workspaceとして受理 | projectlessへ自動変換してhandoff |
| `~/.agents/skills`配下 | workspaceとして受理 | projectlessへ自動変換してhandoff |
| `~/.claude/skills`配下 | workspaceとして受理 | projectlessへ自動変換してhandoff |
| reserved path検出後 | 復旧案なし | 利用者の追加操作なしで継続 |
| prompt表示 | `対象プロジェクト` | `作業対象フォルダー` |
| 自動変換時の副作用 | 未規定 | skill copyなし、選択pathへの書込みなし、projectless contractとhandoffだけ作成 |
| 利用者の復旧操作 | 必須 | 不要 |

## 修正方針

1. activationの最初のplan gateで3種類のruntime skill rootと配下をtask workspaceから外し、`None`へ正規化する。
2. UI、確認画面、Desktop prompt、runtime envelopeの外部名称をtask workspaceへ統一する。
3. Desktop deep linkから`path`／`folder`を省き、projectless新規タスクとして自動継続する。
4. product policyへworkspaceの目的、禁止root、非install、非temporaryを機械可読に追加する。
5. 通常folder、root直下、skill子folder、Codex／Claude、deep link、skill領域への書込みなしを回帰試験する。
6. エラー文ではなく、contract作成からDesktop handoff完了までの実経路を検査する。

## 境界

- 既に`~/.codex/skills`等へ存在するfileの作成者や作成時刻は、本修正では推定しない。
- Skill Magnetは既存fileを削除・移動しない。
- Library Managerの明示的な登録元読取りと隔離authoring transactionは、task activationとは別境界なので禁止しない。
- 成果物の具体的な保存先は各依頼・skill・対象アプリが決める。本修正はruntime skill rootを作業場所として使わせない。
