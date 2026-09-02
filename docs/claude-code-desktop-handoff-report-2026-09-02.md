# Claude Codeデスクトップアプリ handoff対応報告

## 結論

Skill MagnetのClaude実行先をWebブラウザからClaude Desktop内の新規Claude Code sessionへ変更した。製品説明、規範policy、設計文書、実装、Windows/macOS契約テストは、Codex DesktopアプリとClaude Codeデスクトップアプリをターゲットとして一致している。

## 変更内容

- Claude destinationを`https://claude.ai/new`から`claude://code/new`へ変更した。
- 検証済みpromptを`q`、対象projectの絶対pathを`folder`として個別にURL encodeする。
- WindowsではOSのURL handler、macOSでは登録済みURL schemeを通じてClaude Desktopを開く。
- Webブラウザ、clipboard、既存conversation、headless Claude CLIへfallbackしない。
- destination、空prompt、空project、prompt長、URL長、OSによる起動失敗をfail-closedで拒否する。
- handoff準備とOS受理を分離し、受理後だけ`desktop_handoff_ready`を記録する。回答完了は主張しない。

## 表記

製品説明は次の文面へ統一した。

> Skill Magnetは、GitHubをスキルの保管庫として利用し、必要なスキルパックを選んでCodex DesktopアプリまたはClaude Codeデスクトップアプリへ安全に受け渡すローカルツールです。

## 検証

- 製品policyとREADMEの同期テスト
- Claude Code Desktop deep-linkのprompt／folder encoding
- 不正destination、空入力、長さ超過、URL handler失敗時のfail-closed
- Windows/macOS release contractのClaude destination
- Desktop handoff後の証拠状態と一回限りcontract

## 根拠

- [Anthropic: Open Claude Desktop with a link](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link)

