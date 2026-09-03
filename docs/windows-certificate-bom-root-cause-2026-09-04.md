# Windows右クリックメニュー再登録拒否の原因調査

## 事象

Library Manager修正版wheelの導入後、現行configとExplorerメニューが不一致だったため再登録を実行したところ、`Invalid current certificate ownership state`で停止した。

## 原因

`certificate-state.json`のJSON構造と証明書thumbprintは正常だった。ファイル先頭は`EF BB BF`で、Windows PowerShellが保存したUTF-8 BOMである。Python側の所有権復旧処理は`encoding="utf-8"`で読み、`json.loads`へBOMを残したため、正しいJSONを破損扱いした。

兆候だった「ownership state不正」を原因と断定せず、次を順に照合してBOMまで到達した。

1. state fileの存在、内容、thumbprint形式。
2. certificate store ownership flag。
3. file先頭byteと文字encoding。
4. PowerShell writerとPython readerのencoding契約。
5. CI fixtureのencoding。

CIが見逃した原因は、fixtureを`encoding="utf-8"`で生成しており、実Windows PowerShell出力のBOMを再現していなかったことである。

## 修正

- 現在stateとrollback residue内の履歴stateを`utf-8-sig`で読む。BOMあり／なしの両方を受理する。
- 既存の所有権復旧試験をBOM付きfixtureへ変更し、実ファイル形式を固定する。
- 不正JSONや不正thumbprintを拒否するfail-closed境界は変更しない。
