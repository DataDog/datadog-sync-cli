# AWS Integration Sync Capability - Technical Specification

## 1. Overview

This specification defines the requirements and implementation plan for adding AWS Integration configuration synchronization capabilities to the datadog-sync-cli tool. This will allow users to sync AWS Integration configurations between Datadog organizations using the existing `import`, `sync`, `migrate`, and `reset` commands.

## 2. Functional Requirements

### 2.1 Core Resource Support
- **FR-1**: Add a new resource type named `integrations` to the datadog-sync-cli
- **FR-2**: Support a new subtype (name) called `aws` for the `integrations` resource
- **FR-3**: Enable full CRUD operations for AWS Integration configurations through all existing commands

### 2.2 Command Support
- **FR-4**: Support `import` command to retrieve AWS Integration configurations from source organization
- **FR-5**: Support `sync` command to create/update AWS Integration configurations in destination organization
- **FR-6**: Support `migrate` command to perform import followed by sync in one operation
- **FR-7**: Support `reset` command to delete AWS Integration configurations from destination organization
- **FR-8**: Support `diffs` command to show differences between source and destination AWS Integration configurations

### 2.3 Resource Management
- **FR-9**: Handle AWS Account ID as the primary identifier for AWS Integration resources
- **FR-10**: Support filtering AWS Integration resources by Account ID, region, or other attributes
- **FR-11**: Maintain resource relationships and dependencies with existing resources (if any)

### 2.4 Configuration Synchronization
- **FR-12**: Sync all AWS Integration configuration parameters including:
  - Account ID and role name
  - Access credentials (for GovCloud/China accounts)
  - Metrics collection settings
  - Resource collection settings
  - Tag filters and host tags  
  - Excluded regions
  - Namespace-specific rules
  - CSPM resource collection settings

## 3. Non-Functional Requirements

### 3.1 Performance
- **NFR-1**: AWS Integration operations should complete within reasonable time limits (< 30 seconds for typical configurations)
- **NFR-2**: Support concurrent processing when multiple AWS accounts are configured
- **NFR-3**: Implement appropriate rate limiting to respect Datadog API limits

### 3.2 Reliability
- **NFR-4**: Handle API errors gracefully with appropriate retry logic
- **NFR-5**: Provide clear error messages for common failure scenarios (authentication, permissions, etc.)
- **NFR-6**: Ensure idempotent operations that can be safely retried

### 3.3 Security
- **NFR-7**: Handle AWS credentials securely, never logging or exposing sensitive values
- **NFR-8**: Validate required permissions before attempting operations
- **NFR-9**: Follow security best practices for API communication

### 3.4 Compatibility
- **NFR-10**: Maintain backward compatibility with existing datadog-sync-cli functionality
- **NFR-11**: Support all Datadog regions and API endpoints
- **NFR-12**: Follow existing code patterns and conventions used in the codebase

## 4. API Integration Details

### 4.1 Datadog API Endpoints
The implementation will use the following Datadog API endpoints:

- **GET/POST/PUT/DELETE** `/api/v1/integration/aws` - Main AWS Integration configuration
- **GET/POST** `/api/v1/integration/aws/filtering` - AWS tag filtering rules  
- **GET** `/api/v2/integration/aws/available_namespaces` - Available CloudWatch namespaces
- **GET/POST/DELETE** `/api/v1/integration/aws/logs` - AWS Logs integration configuration

### 4.2 Resource Schema
Based on the API documentation, the AWS Integration resource schema includes:

```json
{
  "account_id": "string (required)",
  "role_name": "string (required)", 
  "external_id": "string (optional)",
  "filter_tags": ["string (optional)"],
  "host_tags": ["string (optional)"],
  "account_specific_namespace_rules": {
    "namespace": "boolean"
  },
  "excluded_regions": ["string (optional)"],
  "metrics_collection_enabled": "boolean (optional)",
  "cspm_resource_collection_enabled": "boolean (optional)",
  "extended_resource_collection_enabled": "boolean (optional)",
  "access_key_id": "string (optional, GovCloud/China only)",
  "secret_access_key": "string (optional, GovCloud/China only)"
}
```

## 5. Implementation Plan

### 5.1 File Structure
Following the existing pattern, create these files:

```
datadog_sync/model/integrations.py                    # Main integrations resource class
tests/integration/resources/test_integrations.py      # Integration tests
tests/unit/model/test_integrations.py                 # Unit tests
```

### 5.2 Resource Implementation Details

#### 5.2.1 Base Resource Class
Create `Integrations` class extending `BaseResource`:

```python
class Integrations(BaseResource):
    resource_type = "integrations"
    resource_config = ResourceConfig(
        base_path="/api/v1/integration/aws",
        excluded_attributes=[
            "id",
            "created_at", 
            "modified_at",
            "secret_access_key"  # Security: exclude sensitive fields
        ],
        concurrent=True
    )
```

#### 5.2.2 Subtype Handling
Implement subtype filtering to support the `aws` subtype:
- Filter resources by integration type during import
- Handle AWS-specific API endpoints and schemas
- Support future extensibility for other integration types (azure, gcp, etc.)

#### 5.2.3 Key Methods Implementation

**get_resources()**: Retrieve all AWS integrations from the API
**import_resource()**: Fetch individual AWS integration configuration  
**create_resource()**: Create new AWS integration in destination org
**update_resource()**: Update existing AWS integration configuration
**delete_resource()**: Remove AWS integration from destination org

### 5.3 Testing Strategy

#### 5.3.1 Unit Tests
- Test resource CRUD operations
- Test filtering and subtype handling  
- Test error handling and edge cases
- Mock API responses for isolated testing

#### 5.3.2 Integration Tests  
- End-to-end testing with real API calls (using VCR cassettes)
- Test all commands (import, sync, migrate, reset, diffs)
- Test filtering and resource management
- Test error scenarios and recovery

#### 5.3.3 Test Cases
- Import AWS integrations from source organization
- Sync AWS integrations to destination organization
- Update existing AWS integration configurations
- Delete AWS integrations with reset command
- Filter AWS integrations by account ID or region
- Handle API errors and retry scenarios
- Validate credential security and handling

## 6. Configuration and Usage

### 6.1 Command Examples

```bash
# Import all integrations (including AWS)
datadog-sync import --resources="integrations" \
    --source-api-key="..." --source-app-key="..."

# Import only AWS integrations  
datadog-sync import --resources="integrations" \
    --filter='Type=integrations;Name=name;Value=aws' \
    --source-api-key="..." --source-app-key="..."

# Sync AWS integrations to destination
datadog-sync sync --resources="integrations" \
    --destination-api-key="..." --destination-app-key="..."

# Show differences in AWS integrations
datadog-sync diffs --resources="integrations" \
    --destination-api-key="..." --destination-app-key="..."
```

### 6.2 Filtering Support
- Filter by integration name (subtype): `--filter='Type=integrations;Name=name;Value=aws'`
- Filter by AWS Account ID: `--filter='Type=integrations;Name=account_id;Value=123456789012'`
- Filter by region: `--filter='Type=integrations;Name=excluded_regions;Value=us-west-2'`

## 7. Dependencies and Prerequisites

### 7.1 Required Permissions
The following Datadog permissions are required:
- `aws_configuration_read` - For import and diffs operations
- `aws_configuration_edit` - For sync and reset operations

### 7.2 External Dependencies
- No new external Python dependencies required
- Leverage existing datadog-api-client functionality
- Use existing HTTP client and retry mechanisms

## 8. Risk Assessment and Mitigation

### 8.1 Technical Risks
- **Risk**: API rate limiting during bulk operations
  - **Mitigation**: Implement backoff and retry logic, support concurrent limits
- **Risk**: Credential handling and security
  - **Mitigation**: Follow existing patterns, exclude sensitive fields from logs/state
- **Risk**: Complex AWS account dependencies
  - **Mitigation**: Clear error messages, validation of prerequisites

### 8.2 Operational Risks  
- **Risk**: Breaking existing functionality
  - **Mitigation**: Comprehensive testing, follow existing patterns exactly
- **Risk**: Data loss during sync operations
  - **Mitigation**: Implement backup functionality similar to reset command

## 9. Success Criteria

### 9.1 Functional Success Criteria
- [ ] All four commands (import, sync, migrate, reset) work correctly for AWS integrations
- [ ] Filtering works properly for integrations resource and aws subtype  
- [ ] AWS Integration configurations sync accurately between organizations
- [ ] Error handling provides clear, actionable messages

### 9.2 Quality Success Criteria
- [ ] Unit test coverage >= 95% for new code
- [ ] Integration tests pass for all major scenarios
- [ ] Code review approval from maintainers
- [ ] Documentation updated to include integrations resource

### 9.3 Performance Success Criteria
- [ ] Import/sync operations complete within 30 seconds for typical configurations
- [ ] No performance regression in existing functionality
- [ ] Memory usage remains within acceptable limits

## 10. Future Enhancements

### 10.1 Additional Integration Types
The `integrations` resource is designed to be extensible for future integration types:
- Azure Integration (`name=azure`)
- Google Cloud Integration (`name=gcp`)  
- Other cloud or service integrations

### 10.2 Enhanced Filtering
- Support for more granular filtering options
- Regex-based filtering for complex scenarios
- Multi-attribute filtering combinations

### 10.3 Advanced Features
- Validation of AWS IAM roles and permissions
- Automatic dependency resolution for related resources
- Bulk operations optimization for large numbers of integrations

---

**Document Version**: 1.0  
**Created**: 2024-11-04  
**Status**: Ready for Implementation Review