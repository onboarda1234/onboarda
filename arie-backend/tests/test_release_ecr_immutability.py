from scripts.ops import enforce_ecr_immutability as control


def _state(mutability="MUTABLE", *, account=control.EXPECTED_ACCOUNT_ID):
    calls = []

    def aws_json(arguments):
        calls.append(list(arguments))
        if arguments[:2] == ["sts", "get-caller-identity"]:
            return {"Account": account}
        if arguments[:2] == ["ecr", "describe-repositories"]:
            return {
                "repositories": [
                    {
                        "repositoryName": control.DEFAULT_REPOSITORY,
                        "registryId": control.EXPECTED_ACCOUNT_ID,
                        "repositoryArn": "arn:aws:ecr:af-south-1:782913119880:repository/regmind-backend",
                        "repositoryUri": "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend",
                        "imageTagMutability": mutability,
                        "imageScanningConfiguration": {"scanOnPush": True},
                    }
                ]
            }
        if arguments[:2] == ["ecr", "put-image-tag-mutability"]:
            nonlocal_mutability[0] = "IMMUTABLE"
            return {
                "repositoryName": control.DEFAULT_REPOSITORY,
                "imageTagMutability": "IMMUTABLE",
            }
        raise AssertionError(arguments)

    nonlocal_mutability = [mutability]

    def stateful_aws_json(arguments):
        if arguments[:2] == ["ecr", "describe-repositories"]:
            current = nonlocal_mutability[0]
            original = mutability
            try:
                mutability_value = current
                return {
                    "repositories": [
                        {
                            "repositoryName": control.DEFAULT_REPOSITORY,
                            "registryId": control.EXPECTED_ACCOUNT_ID,
                            "repositoryArn": "arn:aws:ecr:af-south-1:782913119880:repository/regmind-backend",
                            "repositoryUri": "782913119880.dkr.ecr.af-south-1.amazonaws.com/regmind-backend",
                            "imageTagMutability": mutability_value,
                            "imageScanningConfiguration": {"scanOnPush": True},
                        }
                    ]
                }
            finally:
                _ = original
        return aws_json(arguments)

    return stateful_aws_json, calls


def test_immutability_dry_run_is_non_mutating():
    aws_json, calls = _state("MUTABLE")
    result = control.enforce(aws_json=aws_json)
    assert result["mode"] == "dry_run"
    assert result["changed"] is False
    assert not any(call[:2] == ["ecr", "put-image-tag-mutability"] for call in calls)


def test_immutability_apply_is_idempotent_and_verifies_postcondition():
    aws_json, calls = _state("MUTABLE")
    result = control.enforce(
        apply=True,
        confirmation=control.CONFIRMATION,
        aws_json=aws_json,
    )
    assert result["changed"] is True
    assert result["after"]["image_tag_mutability"] == "IMMUTABLE"
    assert sum(call[:2] == ["ecr", "put-image-tag-mutability"] for call in calls) == 1

    already_immutable, second_calls = _state("IMMUTABLE")
    second = control.enforce(
        apply=True,
        confirmation=control.CONFIRMATION,
        aws_json=already_immutable,
    )
    assert second["mode"] == "verified_noop"
    assert second["changed"] is False
    assert not any(
        call[:2] == ["ecr", "put-image-tag-mutability"]
        for call in second_calls
    )


def test_immutability_apply_requires_exact_confirmation_and_account():
    aws_json, _ = _state("MUTABLE")
    try:
        control.enforce(apply=True, confirmation="wrong", aws_json=aws_json)
    except control.ImmutabilityError as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("missing confirmation must fail")

    wrong_account, _ = _state("MUTABLE", account="000000000000")
    try:
        control.enforce(aws_json=wrong_account)
    except control.ImmutabilityError as exc:
        assert "refusing account" in str(exc)
    else:
        raise AssertionError("wrong account must fail")


def test_operator_exposes_no_mutable_rollback_argument():
    actions = control.build_parser()
    option_strings = {
        option
        for action in actions._actions
        for option in action.option_strings
    }
    assert "--mutable" not in option_strings
    assert "--rollback" not in option_strings
