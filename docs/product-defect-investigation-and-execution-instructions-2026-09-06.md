# 製品欠陥の再調査結果・修正実行指示書

調査日：2026-09-06（日本時間）

## 1. 今回の依頼と現在の判定

利用者の最新依頼は「詳細を調査し、指示書MDにまとめて」である。この文書の作成をもって製品修正・実機受入・公開が完了したとはしない。今回の調査では製品コードの追加変更、再ビルド、再導入、UI入力、push、mergeを実行していない。中断前の処理の結果は読み取りで確認した。

**製品完成は未確認。現在の差分には修正候補と検証側の問題が混在する。前担当の『一度再ビルドして一巡すれば終わる』という見通しは採用しない。先に下記の未解決事項を閉じる。**

この文書を、既存の `terra-execution-instructions.md` に対する最新の調査補足・実行順序とする。製品の不変条件、利用者データ保全、公開に必要な実測証拠は既存指示書を維持する。

## 2. 再開位置と中断処理の実測

| 項目 | 今回確認した状態 |
| --- | --- |
| 作業場所 | `C:/Users/HOMEA/.codex/worktrees/dc89/skill-magnet` |
| HEAD | `81dc0e156e63d5bf4480113b8f1b2d0cece6716b` |
| 引継ぎブランチ | `codex/explorer-click-launch-review`（開始時に再確認） |
| リリース候補 | Python 0.5.9 / Appx 0.5.9.0 |
| 中断したビルド・pip・導入・field runner | 対象コマンドの実行中プロセスは今回の照会で見つからなかった |
| 導入済みPython | `C:/Users/HOMEA/AppData/Local/Programs/Python/Python312/Lib/site-packages/skill_magnet` |
| `ui.py` | 導入版と作業ツリーのSHA-256が一致：`cfd947198133b65f7e1c50d2e3e2ae0fecef555940acc1ebf302e393e868a9fb` |
| 導入済みnative source digest | `f067e27538ba935290a53e020858b37d3f040a9f2f501071eb9d09080485ec21` |
| status | `native_build_binding_valid=true`、`usable_installed_state=true`、root action 1、leaf 0 |
| 最後のwheel | 下記パスに存在、SHA-256 `e36c4b682525999a8b78b06e0f8a5d697cef6295772b1f3b49dcc8d1b2866f10` |

```text
C:/Users/HOMEA/AppData/Local/Temp/skill-magnet-final-wheel-1a1af7b5c33f4ef9b532d3b406ef654b/skill_magnet-0.5.9-py3-none-any.whl
```

ツールが「aborted」と表示されても、子処理全体の中止は保証されない。今回、後続の導入状態が変化していた。したがって「中断したから未導入」と決めて同じ処理を再実行してはいけない。上記statusは導入物内部の整合を示すが、作業ツリー全体・wheel全体との一致やExplorer実操作の成功を証明しない。

`python -m build` は `No module named build.__main__` で失敗した実績がある。既存環境では `python -m pip wheel . --no-deps --wheel-dir <専用出力先>` がwheelを生成した。コマンドを未確認のまま切り替え続けない。

## 3. 差分の所有と用途

| ファイル | 現在の変更・確認事項 |
| --- | --- |
| `native/windows-modern-context-menu/SkillMagnetCommand.cpp` | siteから現在folderを取得し、null/0件/現在folderを含む1件配列を背景として処理。入口ログ順序とパス同一性に未解決あり |
| `native/windows-modern-context-menu/ContractTest.cpp` | site付き別folder選択、null、0件、現在folder自身の1件配列を追加。担当エージェントからnative build/contract PASS報告あり。原ログを確認して利用する |
| `src/skill_magnet/ui.py` | `after`取消、chooserのwithdraw/quit、最後の変更でworker終了待ちを撤去。blocked workerを含む終了・画面遷移は未検証 |
| `tests/powershell/windows-explorer-direct-root-field-test.ps1` | 物理クリックの診断、背景点選択、rootの別provider許容、OK操作、Manager終了時モーダル処理を追加 |
| `tests/test_results_gate.py` | 復旧ダイアログ操作の構造検査とクリック失敗診断を追加。実UIテストの不安定な失敗が未解決 |
| `docs/root-cause-windows-context-root-launch-2026-09-05.md` | 背景誤分類の節を追記済み。ただし実績と推論の分類を再修正する必要あり |
| `docs/terra-execution-instructions.md` | 未追跡の旧実行指示書。利用者の引継ぎ資料として保持 |
| `native/windows-modern-context-menu/contract_test.py` | status上変更あり、`git diff --quiet -- <file>` は0。意味差分なし。改行等を確認し不要な変更を混ぜない |

無関係な変更は保持する。開始時のstatusを保存し、作業者の変更と区別する。

## 4. 確定事項と未確定事項

### A. 背景判定と入口診断

確認済み：現在の `Invoke` は `SelectedPath` を呼んだ後で `invoke_enter` を記録している（調査時367行、374行）。対象解決中にCOM呼出しが停止すれば、その要求の入口ログが残らない。`background=false` の初期値から、解決失敗をselectedと誤って記録し得る。

確認済み：1件のpathとsite pathを `CompareStringOrdinal(..., TRUE)` で比較し、一致時に入力pathをsite pathへ置換する。大小文字を区別するfilesystemでは別folderを同一視する可能性がある。

実測：旧fieldは背景右クリックをselectedと記録。0件配列対応だけでは改善せず、同一pathの1件配列対応後は背景の段階を通過した。

**訂正が必要な記述**：これだけで実Explorerが渡した配列の件数・内容を直接観測したとは言えない。既存原因調査書の「1件配列であることが確定」「実績：0件対応後の再試験」は強すぎる。実挙動を説明する有力な推論と、C++ fixtureによる再現を分ける。件数・分類理由の必要最小限の診断を取得できた場合だけ直接実測とする。

修正指示：対象解決前に入口を記録する。入口時のsourceは未判定とし、成功時に確定sourceを記録する。ログ利用側のparserとsequence検証も同時に変更する。case-insensitive文字列一致で異なる対象を置換しない。Shell itemの同一性を確認する方式等を採用し、通常選択、背景、同名大小文字差、失敗時の挙動をテストで固定する。

### B. Library Manager終了失敗

失敗証拠：

```text
C:/Users/HOMEA/AppData/Local/Temp/skill-magnet-field-0.5.9-20260906-u/runner.stderr.log
Window remained visible after close: Library Manager
```

このrunは選択起動・同じfolderの再投入・別folderの背景起動まで進み、Manager終了待ちで失敗した。**Managerの根因は未確定**。

コード上、`library_ui.py::close_manager` はcleanup失敗/非所有folder時に「終了後に復旧できます」というモーダルを出してからdestroyする。この経路は存在するが、run uでそのモーダルが実際に出た証拠は保存されていない。前担当は存在するコード経路を発生原因と扱い、field側へモーダル閉鎖を追加した。この追加だけで製品の停止原因を修正したとは報告しない。

次回の限定再現では、Close送信直前/直後のPID、HWND、visible/enabled、同PIDの全top-level window、処理段階を記録する。次を切り分ける。

1. モーダル表示中：案内されたOKで閉じ、次回起動時に保存状態から復旧できるか。
2. WM_CLOSE未到達：取得したWindowPatternが実際の対象windowに対応しているか。
3. worker待ち：どの処理が中断を観測していないか。
4. Tk callback/複数root：chooserからManagerへ移った時だけ発生するか。
5. OS crash：WERの時刻・PID・moduleと照合する。強制cleanup後のcrashを通常Closeの原因にしない。

### C. chooser終了の修正候補

確認済み：旧候補はworkerが生きている限り25msごとに無期限待ちしていた。最後の変更は待ちを除去し、cancelを立ててwithdraw/quitする。

確認済み：workerはdaemonで、検証と契約準備は別thread、確認契約の永続化はUI側のcallbackにある。ただし各呼出し先がすべて無副作用か、退出後にthreadや隠しTkが残らないかまでは検証されていない。

実績：起動直後Closeの既存テストは成功した。しかしそのテストは通信で停止したworkerやchooser→Managerの同一process遷移を再現しない。

修正指示：非協調workerを注入してCloseが有限時間で完了し、取消後の確認契約永続化・handoffが発生せず、再起動でleaseを取得できるテストを追加する。同一process内でManagerへ移る場合は、旧root・callbackの寿命を検証する。安全性未確認のままdaemonだから終了可能と一般化しない。

### D. fieldクリック検証の欠陥

確認済み：`Invoke-CheckedContextMenuRootPhysicalClick` はroot ancestry不一致でも「座標がroot範囲内、同PID」の別providerを許可する。最終入力判定には実hitのsnapshotを渡すが、意味上のSkill Magnet rootとそのhitの対応を最後まで拘束していない。

修正指示：実Explorerのglyph/provider関係を一度観測し、rootとhitの対応を明示して最終入力時にも検証する。同PIDの任意overlayを許す条件を残さない。既存の最終HWND/PID/process開始時刻/領域/receipt照合を維持する。異常時にmouse inputが発生しないことを確認する。

背景点も「ListItem等でない」だけではtoolbarやscrollbarを排除できない。内容paneの空白であることと、右クリック対象のnative結果を結び付ける。

### E. 不安定テストを反復した問題

該当テスト：`test_native_click_guard_splits_tk_uia_name_from_receipt_semantics`。

既存実行の異なる失敗：

- 正常ケースで`result=False`、`failure=None`、clickedなし。
- 異常ケースで`result=False`、`failure=receipt`なのにclickedあり。

2つ目は「guardがfalseなのにguardがクリックした」とは断定できない。クリック観測fileには入力元・時刻がなく、外部の物理入力、前のbutton状態、対象再生成、provider遷移との因果が未確認である。1つ目の`failure=None`も最終判定前の早期returnまたはcatchを記録できていないことを示す。

修正指示：初期判定と例外にも段階診断を付け、fixtureの入力受付、buttonイベント時刻、guard呼出し前後、故障注入完了を記録する。合格が出るまで繰り返す方式は禁止。故障注入は成功経路に対する変化が確認できる状態で実施する。

## 5. なぜ作業が増えたか

```text
未完成のまま再ビルド・実機試験を反復
├─ 最初の兆候から根因を断定
│  └─ Manager残存 → モーダル原因と推定 → 発生証拠なしでcollector変更
├─ 修正の検証範囲が狭い
│  └─ 起動直後Close成功 → blocked worker/画面遷移も正常と扱う
├─ collectorの制約を緩めて先へ進む
│  └─ root ancestry不一致 → 同PID/範囲で代替 → 証拠の意味が変化
├─ ソースを修正しながら導入と実機試験を並行
│  └─ ContractTest.cppもnative digest対象 → 導入後テスト編集でpreflight不一致
└─ 中断処理の実状態を未確認
   └─ tool abortを未実行と扱う → 重複導入の危険
```

改善の完了条件は反復回数の宣言ではない。「仮説→識別できる観測→限定修正→対応する回帰試験」を1組として記録し、根因未確認の枝を残したまま全体PASSへ進めないことである。

## 6. 次の担当者の実行順序

### 手順1：対象を固定する

status、HEAD、実行中プロセス、導入物を確認する。上記の新しいwheel・導入hashを起点にする。旧digest不一致ログを理由に即再導入しない。既存要件と本書を読み、追加調査をA〜Eに限定する。

### 手順2：未解決コード・fixtureを先に直す

順序はA（入口・対象分類）、C（終了の取消と寿命）、D/E（物理入力の対応・テスト診断）、B（証拠付きのManager限定再現）とする。Bの実機入力が必要な場合だけ、その目的を明記した限定試験を行う。

UI入力を伴う試験を別agent・別processで同時に動かさない。同じデスクトップ、cursor、foregroundを共有するため、互いに失敗を作る。並行化はコードレビュー、ログ読取など入力しない作業に限る。

各失敗について「期待、観測、仮説、反証方法、変更箇所、次の一回で判別すること」を短く記録する。終了時刻・終了コード・stdout/stderrは専用runディレクトリへ残す。例外文を原因調査の結論に転記するだけで終えない。

### 手順3：必要な回帰を確認し、ソースを固定する

| 対象 | 必須確認 |
| --- | --- |
| native | site付き選択/null/0件/現在folder1件、複数件・GetCount等失敗、入口ログが先、対象を別folderへ置換しない |
| chooser | blocked worker中Close、cancel後の書込みなし、通常Close、Managerへの遷移、lease再取得 |
| Manager | 実測で特定した停止原因、案内された復旧操作、再起動成功、保存状態保全 |
| field guard | 正常な実hitは通る、swap/overlay/receipt変更では入力0、失敗理由が観測可能 |
| 重複投入 | 起動中・画面遷移中の同一folder連続要求で主画面/処理が重複しない。別folder要求後も元処理と再試行が成立 |

合格条件に根拠のない全面網羅を追加しない。未解決の重大な経路がある場合は限定試験を追加する。今回の修正と無関係な製品全体の再設計へ広げない。

### 手順4：候補を一つ作って導入する

製品・native contract・collector・原因調査/対応報告本文の変更を揃える。`ContractTest.cpp`もnative入力digestの対象である。導入後に編集するとその実機証跡を再利用できない可能性がある。

既存のビルド方式でwheelを作り、SHAとログを保存して同じwheelを導入する。native buildの所有marker/nonceは既存実装の生成手順を使う。`build.ps1`を引数不足で直接呼ばない。導入status、wheel/runtime全体、native入力との一致を確認する。

### 手順5：正式field、証跡、最終test

既存collectorの`Config`、`InvokeEvidence`、`FieldBundle`を確認して専用出力先で実行する。起動、対象一致、重複抑止、Manager、空folder登録error、runtime skillのprojectless、通常Closeと再起動を全て実測する。

失敗時は失敗runを保持して該当段階だけ診断する。強制killを通常終了として数えない。成功した場合だけ次を更新する。

- `docs/evidence/windows-explorer-direct-root-0.5.9.log`
- `docs/evidence/windows-explorer-direct-root-0.5.9.json`
- `docs/windows-explorer-leaf-launch-results.md`

ledgerの187件は古い。前回80件のresults-gate実行は2失敗で、片方はledger不整合、もう片方は実クリックテスト。observed=409はその時点の発見件数であり、全409件合格の証拠ではない。修正後の全testは `python -m unittest discover -s tests -v` を実施し、実際の実行数・skip・fail・exitを保存する。

release gateの実引数：

```text
python integration/explorer_results_gate.py docs/windows-explorer-leaf-launch-results.md
  --observed-test-count <実件数> --wheel <今回のwheel>
  --invoke-log docs/evidence/windows-explorer-direct-root-0.5.9.log
  --field-evidence docs/evidence/windows-explorer-direct-root-0.5.9.json
```

上は説明用改行。実行時はshellに合った1コマンドにする。Windowsで`--cross-platform-artifact-only`を使ってfield検証を迂回しない。provenance対象文書を後から変更して証跡との対応を壊さないよう、既存gateの入力対象とcommit固定規則を先に確認する。

### 手順6：報告と公開

既存原因調査書はA/Bの断定を修正し、製品欠陥、collector欠陥、未確認の原因を分ける。既存対応報告書へ実装した内容を追記し、検証結果は正本ledgerへ結び付ける。今回の指示書を製品修正報告書の代用にしない。

元の実装依頼を再開する際は、既承認のcommit/push/CI/merge・通常checkout同期まで進む。dirty状態を保全し、CI・保護ルールを迂回しない。今回の文書化依頼だけを根拠に公開操作を実行しない。

## 7. 禁止する引継ぎ判断

- 一部のテストPASS、status=true、version一致を完成と表現する。
- 「同じテストはもうしない」と宣言し、未解決のテスト失敗を放置して導入する。
- モーダルが存在するコードを読んだだけで、今回の停止原因と断定する。
- tool abortを根拠に子処理も止まったと断定する。
- `.codex/skills`、`.agents/skills`、`.claude/skills`へスキルを常設・上書きする。
- 不確かな所要トークン・試験回数・終了時刻を保証する。

## 8. 必要な追加証拠

不明：Manager Close失敗時のwindow一覧と処理段階。run uにはそれを確定できる記録が不足している。

不明：実クリックテストのclicked fileを作った入力。時系列・入力イベントの記録が必要。

不明：最後の変更を含む候補の正式field結果と全件test結果。現在のstatusは代替にならない。

不明：前担当がPASSと報告したnative追加契約の原ログ所在。関連temp出力を一度だけ絞って探し、見つからなければ報告由来と明示する。

不明：push/merge/通常checkout同期の最新状態。本書作成では照会・変更していない。実装再開時に確認する。
