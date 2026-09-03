# `&#x20;` 再発対応報告

日付: 2026-09-03

原因調査: [`root-cause-u0020-recurrence-2026-09-03.md`](root-cause-u0020-recurrence-2026-09-03.md)

## 対応結果

修正前versionが保存したlaunch contractを現行versionでhandoffしても、numeric U+0020参照を実依頼、hash、検証、利用者向け結果へ再露出させない後方互換処理を追加した。

## 実装修正

`src/skill_magnet/activation.py`へ`_effective_actual_request`を追加した。

保存済みcontractのdigestはraw値に対して検証し、検証後に実効依頼を導出する。保存ファイルは書き換えない。導出値を次へ統一して使用する。

- `_task_envelope`
- `_desktop_task_prompt`
- `prepare_codex_desktop_handoff`
- `_output_schema`
- `_verify`
- `_user_result`

これにより、修正前contractでも次の対応が崩れない。

```text
送達した依頼 = SHA-256対象 = schema期待値 = 完了検証対象 = 完了画面の依頼
```

## テスト修正

`tests/test_activation.py`へ、修正前versionが作った有効なcontractを再現するfixtureを追加した。単に不正JSONを差し込まず、raw `purpose`を含むcontract digestを再計算し、完全性検証を通る旧永続状態として試験している。

追加した検証は次の2件である。

1. 旧contractから作るDesktop promptとSHA-256がcanonical依頼を使う。
2. 旧contractをverified runtimeへ実行し、stdin、schema、evidence照合、利用者向け結果まで同じcanonical依頼を使う。

WindowsのANSI code pageにテスト結果が依存しないよう、Python製runtime test doubleはUTF-8 modeで起動する。製品のruntime pipeが既に使用しているUTF-8契約と一致させた変更である。

## 境界

この修正は、Skill Magnetが作成・保存・handoffする初回依頼を対象とする。

既に開いているCodex Desktopタスクの返信欄やresponse annotationはSkill Magnetを経由しないため、このrepositoryから過去メッセージを変更することはできない。新しいSkill Magnet実行で作る初回handoffと、未消費の旧contractについては本修正が有効である。

## 検証結果

- 対象回帰テスト: 3件PASS
- 全テスト: 173件PASS、環境依存1件skip
- 独立wheel build: 2回成功
- 両wheelの論理payload SHA-256: `3edb1a62bb6af10ec3d2906c6c6983dbc1afaa88bc0c1489f3820209d098507b`
- release code: `8ffd77c649a9cd87d0a326661243284485ce5c01`
- local release gate: PASS
- GitHub Actions: branch pushとPRに対するWindows／macOS検証後に最終記録する
