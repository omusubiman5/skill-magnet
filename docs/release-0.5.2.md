# Skill Magnet 0.5.2 リリース候補報告

日付: 2026-09-01

## 判定

- Windowsローカル自己署名版: 0.5.1のGOを維持
- macOSローカル導入版: 実装・CI候補。友人の実MacFinder受入後にGO判定
- 公開署名Macアプリ: 対象外

## 変更

1. macOSの配布形態をPythonライブラリ＋Finder Quick Actionのローカル導入版として固定した。
2. Developer ID、notarization、Mac App Storeをリリース要件から除外した。
3. `context-menu-status --platform macos`を実装した。
4. Finder workflowのplist contract、transaction residue、usable installed stateを検査する回帰テストを追加した。
5. 友人へそのまま渡せる実Mac受入手順を追加した。
6. Python packageを0.5.2、Windows MSIX identityを0.5.2.0へ同期した。

## macOS GO条件

[`macos-local-install-policy.md`](macos-local-install-policy.md)と[`macos-finder-friend-acceptance.md`](macos-finder-friend-acceptance.md)を正本とする。CIだけでFinderメニューの目視表示を代替せず、実Mac受入の記録後に状態を確定する。

## 検証と成果物

- release code commit: `b3b7a3dd176311732341b6ae8cf19cb5cddac1a2`
- full test suite: 140 PASS、環境依存1件skip
- macOS workflow status正常系・改ざん・transaction residue回帰: PASS
- Python 0.5.2 / MSIX 0.5.2.0同期: PASS
- `dist/skill_magnet-0.5.2-py3-none-any.whl`
- wheel SHA-256: `8082dfa1d0c21ca505e8cd1a5fbcc96c6faccd938db4319ceb6a0e461e5c23a2`
- logical payload SHA-256: `c96f964a9799de4fec3240797f4f2b07dae4f3920d531a7632cbcf7651a3ce9d`
