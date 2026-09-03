# Library Manager右クリック連続投入の原因調査

## 結論

右クリック起動直後に画面が空白に見え、同じ項目を再度押せてしまう直接原因は、選択folderの検証・登録をTkの画面構築とevent loop開始より前に同期実行していたことである。さらに、GUI内の`busy`変数は同一process内のbutton連打しか防げず、Explorerから起動される別process同士を制御していなかった。

## 事象から根本原因まで

1. Explorerの右クリック1回ごとに独立したPython processが起動する。
2. 各processはwindowを表示する前にfolder走査、validation、library更新を始める。
3. 利用者には受付・処理中・完了のいずれも見えず、未受付に見える。
4. 再度右クリックすると別processが同じstateとmanaged repositoryを同時操作する。
5. transaction単位の再開制御はあっても、transaction作成前の登録処理にはprocess間排他がなかった。

根本原因は「GUI buttonの二重click」と「OS右クリックによるprocessの多重起動」を同じ問題として扱い、後者に必要なprocess間lockと受付表示を設計していなかったことである。

## 失敗ツリー

- 二重処理が発生する
  - 同一process内
    - buttonが処理中も有効
    - callback再入
  - 別process間
    - 同一folderの連続右クリック
    - 別folderの並行右クリック
    - 強制終了後の残存lockを生存processと誤認
- 利用者が連続投入する
  - window表示前に重い処理
  - 現在の処理名がない
  - 操作不能状態が視覚化されない
- 復旧不能になる
  - PIDだけで生存判定する
  - stale lock fileを永久lockとして扱う
  - 未完了transactionを新規transactionで上書きする

## 修正判断

PID検査とlock file削除による排他は採用しない。PID再利用、権限差、削除競合があり、異常終了時の正しさをアプリ側で証明しにくいためである。Windowsでは`msvcrt`、POSIXでは`flock`の非blocking OS file lockを保持する。process終了時にはOSがlockを解放するため、lock fileが残っても次回processは取得できる。
