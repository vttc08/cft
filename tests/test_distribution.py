from datetime import datetime, timezone

from cft.models.distribution import normalize_distribution


def test_normalize_distribution_extracts_stage_one_fields() -> None:
    modified = datetime(2026, 5, 1, tzinfo=timezone.utc)

    distribution = normalize_distribution(
        {
            "Id": "E123",
            "ARN": "arn:aws:cloudfront::123456789012:distribution/E123",
            "Comment": "api",
            "DomainName": "d111.cloudfront.net",
            "Enabled": True,
            "Status": "Deployed",
            "LastModifiedTime": modified,
            "Aliases": {"Quantity": 1, "Items": ["cdn.example.com"]},
            "Origins": {
                "Quantity": 1,
                "Items": [{"Id": "origin", "DomainName": "origin.example.com"}],
            },
        }
    )

    assert distribution.distribution_id == "E123"
    assert distribution.comment == "api"
    assert distribution.domain_name == "d111.cloudfront.net"
    assert distribution.enabled is True
    assert distribution.status == "Deployed"
    assert distribution.aliases == ("cdn.example.com",)
    assert distribution.origins == ("origin.example.com",)
    assert distribution.last_modified_time is modified
