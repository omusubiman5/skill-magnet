# 「本来のユーザー依頼への適用未実装」原因調査

## 2026-08-28 起動失敗の再調査

### 2026-08-28 00:25 追加事実

- Explorer実画面では、レジストリを個別skillへ更新しExplorerを再起動しても、旧 `Skill Magnet → Pack: codex-pmo-skills (9 skills)` が表示された。
- 現行HKCRのclassic subtreeには `Pack:` 値が存在せず、個別skill commandだけが存在する。
- 未登録状態の旧native資材を正規アンインストールし、classic canonical keyも `SkillMagnetClassic` へ分離したが、旧Packメニューは残った。
- よって旧Packメニューは現行classic subtreeとは別のshell handler登録から供給されている。登録元の特定と除去は継続中。

### 判定

**未解決・実画面再検証待ち。** 過去の単体テスト、JSON、レジストリ登録結果を起動成功またはskill実行成功の証拠として扱わない。

### 実画面で確認した事実

1. 修正前のWindows 11通常メニューにSkill Magnetは表示されなかった。
2. クラシックメニューでは `Skill Magnet → Pack: codex-pmo-skills → Codex / Claude` と表示された。
3. 登録設計上存在する9個の `Skill: <id>` が実メニューから消えていた。
4. Codexを選択しても依頼入力画面もTerminalも起動しない事象を再現した。
5. 登録されていた `pythonw.exe` は同一引数で終了コード1、画面なしで終了した。`python.exe` は同一引数でSkill Magnet入力画面まで到達した。

### 原因仮説と切り分け

- 確定原因: `pythonw.exe` が起動例外を不可視化し、利用者には無反応に見えていた。
- 有力原因: `Skill Magnet → pack → skill → runtime` の4階層レジストリ構造がExplorerの実表示・実行可能階層を超え、skill階層が欠落した。
- 未確定部分: 3階層へ平坦化した後のExplorer実起動。2026-08-28のスクリーンショット再試験はComputer Useが `0x80070005` で停止したため未確認。

### 証拠基準

今後は次を満たす同一試験のスクリーンショットがなければ成功としない。

1. Explorerに個別skill名が表示されている。
2. 選択したskill名とactual requestが入力・確認画面に表示されている。
3. Terminalにskill実行開始が表示されている。
4. Terminalに選択skill名、実行結果、`verified_completed` が表示されている。

途中状態、単体テスト、JSON evidence、プロセス一覧、レジストリ値だけで完了判定しない。

調査日: 2026-08-27（2026-08-28追記）

## 事象

Windows Explorerでskillを選択すると、Codexは起動し、選択skillの読込・適用証拠も `verified_applied` になった。しかし、表示された結果はpack定義の一般purposeに対する固定判定であり、その後にユーザーがCodexへ入力する本来の依頼はSkill Magnetの検証対象になっていなかった。

これは成果物生成の成功ではない。ユーザー依頼を受け取る前に完了判定していたため、製品の「ユーザーのタスクと検証済みinstructionを同じtask envelopeへ入れる」という完了条件を満たしていなかった。

## 確認した事実

1. Windowsの `context` 経路は、依頼入力UIである `show_context_selection` を迂回して `launch_context_leaf` を直接呼んでいた。
2. `launch_context_leaf` は `context_selection_details` の `purpose`、すなわちpack定義の固定purposeをlaunch contractへ入れていた。
3. Codexはその固定purposeを正常に実行し、skill固有acceptanceを通過していた。このため技術的には正しい `verified_applied` でも、ユーザー成果物としては誤った成功だった。
4. 検証output schemaの `result` はskill固有の固定判定フィールドしか許可せず、ユーザー依頼の成果物を格納するフィールドがなかった。
5. 完了済みsessionを対話画面へresumeした後の新規入力は、Skill Magnetのcontract、prompt digest、acceptance、terminal eventの対象外だった。

## 根本原因

根本原因は、次の異なる二つの処理を同一視したことにある。

- 選択skillを読み、skill固有acceptanceを満たせることの自己検査
- 選択skillをユーザーの実依頼へ適用し、成果物を生成すること

旧実装は前者だけを実行し、その結果を後者の成功として表示していた。テストも固定purposeとskill固有定数の一致を中心にしており、「実依頼がpromptへ入り、その成果物が検証outputへ残る」という必須条件を持っていなかった。

加えて、旧 `skill_specific_application_evidence` の値はskill実行イベントではなく、`acceptance.json` の定義内容から計算したdigestだった。これは「どのacceptanceを使ったか」は示すが、「選択skillの実行が完了したか」は示さない。このdigestとacceptance一致だけでterminal `verified_applied` を発行していたことが、完了判定の直接的な欠陥である。

したがって、過去に保存された `verified_applied` は適用検査の履歴としてのみ扱い、現在のskill実行完了証拠には使用しない。

## 切り分け結果

- Codex認証・API通信: 正常
- Skill Magnetから実Codexへのschema付き実行: 正常
- 同一Codex sessionの対話handoff: 正常
- Explorer旧登録: 更新前は現行引数不足でAI到達前に失敗
- 本来の依頼取得: Windows経路で未実装
- 本来の依頼の成果物保持: output schema上で未実装
- Claude: OAuth access token期限切れによる401。今回の設計欠陥とは別の既知ブロッカー

## 修正方針

1. Windowsでも実行前にactual requestを必須入力させる。
2. 選択pack、skill、runtime、commit、digest、project、actual requestを確認してからcontractを作る。
3. actual requestを `PURPOSE` としてtask envelopeへ含め、「デモではなく依頼を完了する」ことを明記する。
4. `result.task_output` を必須化し、空成果物を成功扱いしない。
5. skill固有acceptanceと `task_output` に加え、選択skill ID・`completed` status・actual-request SHA-256が一致する明示的completion evidenceが揃った場合だけ `verified_completed` とする。
6. completion evidenceが欠落、不一致、別依頼への紐付き、または空成果物の場合はterminal successを禁止する。

## 非原因

画面に出たCloudflare/Unreal MCPの警告は、未認証または未起動MCP serverの警告である。選択skillの読込、actual requestの実行、skill固有acceptanceには使用しておらず、今回の根本原因ではない。
# 2026-08-27 実画面再調査（完了判定禁止）

- Windows 11 の通常右クリックメニューには Skill Magnet が表示されなかった。
- クラシックメニューを再登録すると「その他のオプション」に Skill Magnet は表示された。
- その実メニューから Codex を選択しても、依頼入力画面も Terminal も表示されない事象を再現した。
- 登録コマンドの起動ファイルは `pythonw.exe` だった。同一引数を `pythonw.exe` で起動すると終了コード 1、ウィンドウなしで終了した。
- 同一引数を `python.exe` で起動すると Skill Magnet ウィンドウまで到達した。

したがって「メニュー登録成功」や JSON evidence は skill 実行完了の証拠ではない。実画面で Terminal に skill 実行結果が表示されるまでは未完了とする。
