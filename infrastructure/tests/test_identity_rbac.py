from pathlib import Path

import pytest

MODULES = Path(__file__).resolve().parents[1] / "modules"
MAIN = MODULES.parent / "main.bicep"


def read_module(name: str) -> str:
    return (MODULES / name).read_text(encoding="utf-8")


def test_user_assigned_identity_exposes_runtime_and_rbac_ids() -> None:
    identity = read_module("user-assigned-identity.bicep")

    assert "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31" in identity
    assert "output identityId string = identity.id" in identity
    assert "output principalId string = identity.properties.principalId" in identity
    assert "output clientId string = identity.properties.clientId" in identity


def test_acr_pull_assignment_is_registry_scoped_and_deterministic() -> None:
    assignment = read_module("acr-pull-role-assignment.bicep")

    assert "'7f951dda-4ed3-4680-a7ca-43fe172d538d'" in assignment
    assert "name: guid(registry.id, principalId, acrPullRoleDefinitionId)" in assignment
    assert "scope: registry" in assignment
    assert "principalType: 'ServicePrincipal'" in assignment


def test_search_roles_are_limited_to_required_data_access() -> None:
    assignment = read_module("search-index-role-assignment.bicep")

    assert "'reader'" in assignment
    assert "'indexContributor'" in assignment
    assert "'1407120a-92aa-4202-b7e9-c0e197c71c8f'" in assignment
    assert "'8ebe5a00-799e-43f5-93ac-243d3dce84a7'" in assignment
    assert "scope: searchService" in assignment


def test_storage_roles_are_limited_to_one_blob_container() -> None:
    assignment = read_module("storage-container-role-assignment.bicep")

    assert "'2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'" in assignment
    assert "'ba92f5b4-2d11-453d-a403-e96b0029c9fe'" in assignment
    assert "scope: blobContainer" in assignment
    assert "name: blobContainerName" in assignment


def test_foundry_grants_foundry_user_for_direct_inference_at_project_scope() -> None:
    assignment = read_module("foundry-user-role-assignment.bicep")

    # Foundry User (formerly Azure AI User) covers direct chat and embedding inference.
    assert "'53ca6127-db72-4b80-b1b0-d745d6d5456d'" in assignment
    # Foundry Agent Consumer only reaches published agent endpoints; it would deny them.
    assert "'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'" not in assignment
    assert "scope: foundryProject" in assignment
    assert "scope: foundryAccount" not in assignment


def test_every_inference_caller_gets_project_scoped_foundry_user() -> None:
    main = MAIN.read_text(encoding="utf-8")

    for caller in ("api", "worker", "evaluation"):
        assert (
            f"module {caller}FoundryAccess './modules/foundry-user-role-assignment.bicep'" in main
        )
    assert "foundry-agent-consumer-role-assignment.bicep" not in main


def test_queue_roles_are_limited_to_one_queue() -> None:
    assignment = read_module("storage-queue-role-assignment.bicep")

    assert "'8a0f0c08-91a1-4084-bc3d-661d67233fed'" in assignment  # processor
    assert "'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'" in assignment  # sender
    assert "scope: queue" in assignment


def test_telemetry_publisher_is_scoped_to_application_insights() -> None:
    assignment = read_module("monitoring-publisher-role-assignment.bicep")

    assert "'3913510d-42f4-4b42-8a1d-c0b25e3be3c6'" in assignment
    assert "scope: applicationInsights" in assignment


def test_only_the_deployer_may_manage_search_index_definitions() -> None:
    assignment = read_module("search-index-role-assignment.bicep")
    main = MAIN.read_text(encoding="utf-8")

    assert "'7ca78c08-252a-4471-8644-bb5ff32d4ba0'" in assignment
    assert main.count("accessLevel: 'serviceContributor'") == 1
    assert "principalId: deploymentPrincipalId" in main


def test_api_has_no_storage_access_and_worker_storage_access_is_split() -> None:
    main = MAIN.read_text(encoding="utf-8")

    assert "module apiBlobAccess" not in main
    assert "blobContainerName: incomingContainerName" in main
    assert "blobContainerName: documentsContainerName" in main


@pytest.mark.parametrize(
    "module_name",
    [
        "acr-pull-role-assignment.bicep",
        "search-index-role-assignment.bicep",
        "storage-container-role-assignment.bicep",
        "storage-queue-role-assignment.bicep",
        "monitoring-publisher-role-assignment.bicep",
        "foundry-user-role-assignment.bicep",
    ],
)
def test_role_assignment_modules_expose_resource_ids(module_name: str) -> None:
    assert "output roleAssignmentId string = " in read_module(module_name)
