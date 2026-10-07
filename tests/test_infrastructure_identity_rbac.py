from pathlib import Path


INFRASTRUCTURE = Path(__file__).resolve().parents[1] / "infrastructure" / "modules"


def read_module(name: str) -> str:
    return (INFRASTRUCTURE / name).read_text(encoding="utf-8")


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


def test_search_role_options_are_limited_to_required_data_access() -> None:
    assignment = read_module("search-index-role-assignment.bicep")

    assert "'reader'" in assignment
    assert "'indexContributor'" in assignment
    assert "'1407120a-92aa-4202-b7e9-c0e197c71c8f'" in assignment
    assert "'8ebe5a00-799e-43f5-93ac-243d3dce84a7'" in assignment
    assert "scope: searchService" in assignment


def test_storage_role_options_are_limited_to_one_blob_container() -> None:
    assignment = read_module("storage-container-role-assignment.bicep")

    assert "'2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'" in assignment
    assert "'ba92f5b4-2d11-453d-a403-e96b0029c9fe'" in assignment
    assert "scope: blobContainer" in assignment
    assert "name: blobContainerName" in assignment


def test_foundry_access_uses_agent_consumer_at_project_scope() -> None:
    assignment = read_module("foundry-agent-consumer-role-assignment.bicep")

    assert "'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'" in assignment
    assert "scope: foundryProject" in assignment
    assert "Foundry User" not in assignment


def test_all_role_assignments_return_the_assignment_resource_id() -> None:
    module_names = (
        "acr-pull-role-assignment.bicep",
        "search-index-role-assignment.bicep",
        "storage-container-role-assignment.bicep",
        "foundry-agent-consumer-role-assignment.bicep",
    )

    for module_name in module_names:
        assert "output roleAssignmentId string = " in read_module(module_name)
