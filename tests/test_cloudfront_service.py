from cft.aws.cloudfront import CloudFrontInventoryService


class FakePaginator:
    def paginate(self) -> list[dict[str, object]]:
        return [
            {
                "DistributionList": {
                    "Items": [
                        {
                            "Id": "E123",
                            "Comment": "site",
                            "DomainName": "d111.cloudfront.net",
                            "Enabled": True,
                            "Status": "Deployed",
                        }
                    ]
                }
            }
        ]


class FakeCloudFrontClient:
    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_distributions"
        return FakePaginator()


class FakeStsClient:
    def get_caller_identity(self) -> dict[str, str]:
        return {
            "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/test",
            "UserId": "AIDA",
        }


class FakeSession:
    profile_name = "dev"

    def client(self, service_name: str) -> object:
        if service_name == "sts":
            return FakeStsClient()
        if service_name == "cloudfront":
            return FakeCloudFrontClient()
        raise AssertionError(service_name)


def test_cloudfront_inventory_service_reads_identity_and_distributions() -> None:
    service = CloudFrontInventoryService()

    identity = service._get_identity(FakeSession())  # type: ignore[arg-type]
    distributions = service._list_distributions(FakeSession())  # type: ignore[arg-type]

    assert identity.account_id == "123456789012"
    assert len(distributions) == 1
    assert distributions[0].distribution_id == "E123"
