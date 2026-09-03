# 選択フォルダーからのスキル登録スモークテスト

## 発見した問題

`C:\Users\HOMEA\.codex\skills\cma-004`を右クリック登録すると、`Skill cma-004 must define trigger and boundary`で停止した。

`cma-004`には、frontmatterのdescriptionへ「依頼された時に使う」、本文へ`## 制約`と「行わない」が記載されていた。原因はスキル側の不足ではなく、Library Managerが`trigger`／`boundary`など限られた固定語だけで判定していたことである。

## 修正

- 「とき／時／場合に使う」「依頼・指定・必要となる時」を適用条件として認識する。
- `制約`、`禁止事項`、`対象外`、`非対象`の見出しを境界として認識する。
- 「行わない」「してはならない」「禁止する」「対象外とする」を境界として認識する。
- 適用条件・境界の両方が本当にないスキルは引き続き拒否する。

## スモークテスト

1. 実ファイル`cma-004/SKILL.md`を読み取る。
2. 現在のユーザーlibraryを隔離コピーする。
3. 実`cma-004`フォルダーを隔離コピーへ登録する。
4. `custom-skills`パックへ`cma-004`が追加されることを確認する。
5. 自動生成された`acceptance.json`を含め、library全体を再検証する。
6. 元のユーザーlibraryが変更されていないことを確認する。

結果は、1 skill登録、library全体22 skills、validation PASSだった。

## 誤操作・強制終了シミュレーション

- 登録開始前に閉じる: libraryへの書込みなし。
- 候補コピー作成中に閉じる: 正本libraryは変更されない。
- 正本をbackupへ移動した直後に閉じる: 次回起動時に検証済みbackupを自動復旧する。
- 登録完了後、完了メッセージ前に閉じる: 登録内容は保持され、再登録は`already_registered`の正常終了になる。
- 検証エラーで閉じる: 候補だけを破棄し、正本manifestが不変であることを確認する。

## リリース候補

- release code: `3be279210f724ec3270fe3c8e06e528dcdb9e808`
- wheel論理payload SHA-256: `fbfe1f67087ef4c5544b691ee7bcddf91d21ee6a7b6db2e40f68affbc78c8b32`
