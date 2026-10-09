# Security Policy

**English** | [繁體中文](#繁體中文)

OTD Area Calculator is a locally run Windows desktop tool. It does not require a
network connection and should not collect or upload user data.

## Versions and report details (English)

Please identify the release or commit, Windows/Python version, affected behavior and
steps to reproduce in your private report. Include only the minimum sanitized evidence.
Check whether the issue still occurs on the latest `main`.

## Reporting a vulnerability (English)

If you discover a security issue, please report it privately rather than opening a
public issue:

- Use GitHub's **private vulnerability reporting / Security Advisory** feature on this
  repository: [Report a vulnerability](https://github.com/Alex123666tw/tablet-area-calculator/security/advisories/new)
  (Security tab -> Report a vulnerability).

For non-sensitive bugs and feature requests, please open a regular GitHub issue.

Please do not post exploitable details, personal diagnostic output, hardware unique
identifiers, or raw packet logs in a public issue.

## User data (English)

Please do **not** commit the following to a public repository:

- `debug_packets.txt`
- `detected_tablets.txt`
- `build/` or `dist/` artifacts
- Any diagnostic output containing local paths, hardware unique IDs, tokens, keys,
  or account data.

---

<a name="繁體中文"></a>

## 繁體中文

本專案是本機執行的 Windows 桌面工具，不需要網路連線，也不應收集或上傳使用者資料。

## 版本與回報資訊

私密回報請註明版本或 commit、Windows／Python 版本、受影響的行為與重現步驟，
只附上去除個人資訊後的最小證據。請確認最新 `main` 是否仍有問題。

## 回報安全問題

如果你發現安全問題，請以私密方式回報，不要在公開 issue 中揭露：

- 使用本 repository 的 GitHub **私密漏洞回報 / Security Advisory** 功能
  （Security 分頁 -> Report a vulnerability）：[私密回報入口](https://github.com/Alex123666tw/tablet-area-calculator/security/advisories/new)。

一般（非敏感）的錯誤回報與功能建議，請開一般的 GitHub issue。

請不要在公開 issue 中貼出可被濫用的細節、個人診斷輸出、硬體唯一識別資訊或原始封包紀錄。

## 使用者資料

請不要提交以下資料到公開 repository：

- `debug_packets.txt`
- `detected_tablets.txt`
- `build/` 或 `dist/` 產物
- 含本機路徑、硬體唯一 ID、token、金鑰或帳號資料的診斷輸出
