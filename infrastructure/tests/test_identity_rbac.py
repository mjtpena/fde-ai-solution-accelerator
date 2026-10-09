import unittest
from pathlib import Path

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

    def test_queue_roles_are_limited_to_one_queue(self) -> None:
        assignment = self.read_module("storage-queue-role-assignment.bicep")

        self.assertIn("'8a0f0c08-91a1-4084-bc3d-661d67233fed'", assignment)  # processor
        self.assertIn("'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a'", assignment)  # sender
        self.assertIn("scope: queue", assignment)

    def test_telemetry_publisher_is_scoped_to_application_insights(self) -> None:
        assignment = self.read_module("monitoring-publisher-role-assignment.bicep")

        self.assertIn("'3913510d-42f4-4b42-8a1d-c0b25e3be3c6'", assignment)
        self.assertIn("scope: applicationInsights", assignment)

    def test_only_the_deployer_may_manage_search_index_definitions(self) -> None:
        assignment = self.read_module("search-index-role-assignment.bicep")
        main = (MODULES.parent / "main.bicep").read_text(encoding="utf-8")

        self.assertIn("'7ca78c08-252a-4471-8644-bb5ff32d4ba0'", assignment)
        self.assertEqual(main.count("accessLevel: 'serviceContributor'"), 1)
        self.assertIn("principalId: deploymentPrincipalId", main)

    def test_api_has_no_storage_access_and_worker_storage_access_is_split(self) -> None:
        main = (MODULES.parent / "main.bicep").read_text(encoding="utf-8")

        self.assertNotIn("module apiBlobAccess", main)
        self.assertIn("blobContainerName: incomingContainerName", main)
        self.assertIn("blobContainerName: documentsContainerName", main)

    def test_role_assignment_modules_expose_resource_ids(self) -> None:
        module_names = (
            "acr-pull-role-assignment.bicep",
            "search-index-role-assignment.bicep",
            "storage-container-role-assignment.bicep",
            "storage-queue-role-assignment.bicep",
            "monitoring-publisher-role-assignment.bicep",
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
