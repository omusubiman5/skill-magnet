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

