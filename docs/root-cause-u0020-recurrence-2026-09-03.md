# `&#x20;` 再発の原因調査

日付: 2026-09-03

## 対象事象

Codex Desktopのタスクで、選択した見出しの後ろに次のliteral文字列が表示された。

```text
（私が最も見たくない結論）&#x20;
```

## 結論

2026-09-01の修正は新規activation planの作成時にだけ実依頼を正規化していた。修正前に作られた有効なlaunch contractを更新後のアプリが読み、Desktop promptまたは検証runtimeへ渡す場合、保存済み`purpose`をそのまま使うため`&#x20;`が再露出できた。

また、Skill Magnetが制御するのは新規Codex Desktopタスクへの最初のhandoffまでである。handoff後にCodex Desktop上で入力された返信、回答選択、response annotationはSkill Magnetを通らない。現在のタスク内で作られた後続メッセージは、この境界外経路に該当する。

この2経路を同一原因として扱わない。

## 原因ツリー

```text
literal &#x20; がCodex Desktopのメッセージに現れる
├─ Skill Magnetの初回handoff
│  ├─ 新規contract
│  │  └─ plan境界で正規化済み
│  └─ 修正前に保存された旧contract
│     └─ handoff・hash・検証でraw purposeを再使用していた ← 製品側の未修正経路
└─ handoff後の同一タスクへの返信
   ├─ 手入力・貼り付け
   └─ Codex Desktopの回答選択／annotation生成
      └─ Skill Magnetのprocessを通らない ← Codex Desktop側の境界
```

## なぜ前回のテストで検出できなかったか

前回テストは、entityを含む入力から新しいplanとcontractを作り、そのcontractをhandoffする正常系だった。Validator、contract、promptが同じ実行内で生成されるため、新規contractの正規化は確認できた。

一方、次の状態遷移が欠けていた。

1. 旧versionがliteral entityを含むcontractを署名・保存する。
2. アプリだけを更新する。
3. 更新後versionが旧contractの完全性を検証する。
4. 旧contractをDesktopまたはverified runtimeへ送る。
5. prompt、SHA-256、output schema、完了検証、利用者向け結果が同じcanonical依頼を使う。

つまり、入力表現のテストはあったが、version境界をまたぐ永続状態のテストがなかった。

## 影響範囲

- 修正前versionで作成され、未消費のlaunch contract
- 旧contractから作るCodex Desktop／Claude Code Desktop prompt
- verified runtimeのstdin prompt、actual-request SHA-256、output schema、完了検証、利用者向け結果

次はSkill Magnet側では修正できない。

- handoff済みのCodex Desktopタスク内に既に表示されたメッセージ
- Codex Desktopが回答選択から生成するresponse annotation
- Codex Desktopへ直接入力・貼り付けされた文字列

## 修正方針

保存済みcontractはdigestで保護されているため、読み込み時に書き換えない。完全性確認後に`normalize_actual_request`から実効依頼を導出し、次の全境界で同じ値を使う。

- runtime task envelope
- Desktop task prompt
- actual-request SHA-256
- output schema
- completion evidence照合
- 利用者向け完了結果

この方式なら旧contractの署名内容を破壊せず、送達後の値だけを現行canonicalization規則へ統一できる。

## 完了条件

1. 旧形式の有効なcontractを再現できる。
2. 旧contract自体のdigest検証は維持される。
3. Desktop promptにliteral `&#x20;`が残らない。
4. prompt、hash、schema、検証、結果表示が同じcanonical依頼を使う。
5. 新規contractの既存回帰テストが維持される。
6. 全テストとrelease gateがPASSする。

