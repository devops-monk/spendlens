# Vendored detection mods

Turbot's Tailpipe detection mods, copied unchanged from their release tags (Apache-2.0; each keeps its LICENSE).
`spendlens security detections` runs them with Powerpipe against Tailpipe's local database.

| Folder | Source | Version |
|---|---|---|
| `cloudtrail/` | https://github.com/turbot/tailpipe-mod-aws-cloudtrail-log-detections | v0.3.0 |
| `s3_access/` | https://github.com/turbot/tailpipe-mod-aws-s3-server-access-log-detections | v0.2.0 |
| `flow_logs/` | https://github.com/turbot/tailpipe-mod-aws-vpc-flow-log-detections | v0.1.0 |

To update: `git archive <tag>` from the mod's repository into the folder, then remove `.github/`, `README.md`,
`CHANGELOG.md` and `docs/images/`. Keep the rest, including every `docs` folder: the mod loads those files.
Update the versions here, in `log_detections.py` and in NOTICE.
