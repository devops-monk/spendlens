"""Tag check definitions shared by the mod generator and the `spendlens tags` command."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TagCheck:
    title_suffix: str
    description: str
    benchmark_title: str
    benchmark_description: str
    param: str | None  # mod variable passed as $1
    severity: str


TAG_CHECKS: dict[str, TagCheck] = {
    "mandatory": TagCheck(
        "should have mandatory tags",
        "Check if {plural} have all mandatory tags.",
        "Mandatory tags",
        "Resources must carry the company's mandatory tags (cost allocation, ownership, environment).",
        "mandatory_tags",
        "high",
    ),
    "untagged": TagCheck(
        "should be tagged",
        "Check if {plural} have at least 1 tag.",
        "Untagged resources",
        "Resources with no tags at all cannot be allocated to a team or cost centre.",
        None,
        "medium",
    ),
    "prohibited": TagCheck(
        "should not have prohibited tags",
        "Check if {plural} use any prohibited tag keys.",
        "Prohibited tags",
        "Resources must not use prohibited tag keys (e.g. secrets in tags, deprecated keys).",
        "prohibited_tags",
        "medium",
    ),
    "limit": TagCheck(
        "should not exceed the tag limit",
        "Check if the number of tags on {plural} is within the limit.",
        "Tag limit",
        "Resources should stay under the tag limit so required tags can always be added.",
        "tag_limit",
        "low",
    ),
    "expected_values": TagCheck(
        "should have expected tag values",
        "Check if {plural} use allowed values for governed tag keys.",
        "Expected tag values",
        "Governed tag keys must use allowed values (SQL LIKE patterns supported).",
        "expected_tag_values",
        "medium",
    ),
}
