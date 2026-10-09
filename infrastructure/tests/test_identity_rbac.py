from pathlib import Path
import unittest


MODULES = Path(__file__).resolve().parents[1] / "modules"


class IdentityRbacContractTests(unittest.TestCase):
    def read_module(self, name: str) -> str:
        return (MODULES / name).read_text(encoding="utf-8")

    def test_user_assigned_identity_exposes_runtime_and_rbac_ids(self) -> None:
        identity = self.read_module("user-assigned-identity.bicep")

        self.assertIn(
            "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31",
            identity,
        )
        self.assertIn("output identityId string = identity.id", identity)
        self.assertIn(
            "output principalId string = identity.properties.principalId",
            identity,
        )
        self.assertIn("output clientId string = identity.properties.clientId", identity)

    def test_acr_pull_assignment_is_registry_scoped_and_deterministic(self) -> None:
        assignment = self.read_module("acr-pull-role-assignment.bicep")

        self.assertIn("'7f951dda-4ed3-4680-a7ca-43fe172d538d'", assignment)
        self.assertIn(
            "name: guid(registry.id, principalId, acrPullRoleDefinitionId)",
            assignment,
        )
        self.assertIn("scope: registry", assignment)
        self.assertIn("principalType: 'ServicePrincipal'", assignment)

    def test_search_roles_are_limited_to_required_data_access(self) -> None:
        assignment = self.read_module("search-index-role-assignment.bicep")

        self.assertIn("'reader'", assignment)
        self.assertIn("'indexContributor'", assignment)
        self.assertIn("'1407120a-92aa-4202-b7e9-c0e197c71c8f'", assignment)
        self.assertIn("'8ebe5a00-799e-43f5-93ac-243d3dce84a7'", assignment)
        self.assertIn("scope: searchService", assignment)

    def test_storage_roles_are_limited_to_one_blob_container(self) -> None:
        assignment = self.read_module("storage-container-role-assignment.bicep")

        self.assertIn("'2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'", assignment)
        self.assertIn("'ba92f5b4-2d11-453d-a403-e96b0029c9fe'", assignment)
        self.assertIn("scope: blobContainer", assignment)
        self.assertIn("name: blobContainerName", assignment)

    def test_foundry_uses_agent_consumer_at_project_scope(self) -> None:
        assignment = self.read_module("foundry-agent-consumer-role-assignment.bicep")

        self.assertIn("'eed3b665-ab3a-47b6-8f48-c9382fb1dad6'", assignment)
        self.assertIn("scope: foundryProject", assignment)
        self.assertNotIn("Foundry User", assignment)

    def test_role_assignment_modules_expose_resource_ids(self) -> None:
        module_names = (
            "acr-pull-role-assignment.bicep",
            "search-index-role-assignment.bicep",
            "storage-container-role-assignment.bicep",
            "foundry-agent-consumer-role-assignment.bicep",
        )

        for module_name in module_names:
            with self.subTest(module=module_name):
                self.assertIn(
                    "output roleAssignmentId string = ",
                    self.read_module(module_name),
                )


if __name__ == "__main__":
    unittest.main()
