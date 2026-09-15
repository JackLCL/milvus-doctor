# Minimum read-only access

## Kubernetes

Ask the cluster administrator to provide a credential restricted to the selected
namespace and these resources. This is a configuration example for the USER to
review/apply, not an action for Doctor. Use Role/RoleBinding, not cluster-admin.

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: milvus-doctor-reader
  namespace: REPLACE_WITH_SELECTED_NAMESPACE
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "persistentvolumeclaims", "events"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets"]
    verbs: ["get", "list"]
  - apiGroups: ["milvus.io"]
    resources: ["milvuses"]
    verbs: ["get", "list"]
```

Bind this Role to a dedicated user/ServiceAccount using your organization's
standard access workflow. The Operator rule is only needed for Operator mode.
No Secrets, pods/exec, ConfigMaps, cluster-scoped Nodes or write verbs are needed.
Normal metadata diagnosis does not require pods/log. Some checks remain unavailable with fewer privileges; report that
gap instead of escalating. Doctor should not use a user's general admin session.

### Optional log-reading permission

Only when the user chooses [log export](log-export.md), ask the administrator to
add the following **namespace Role** rule to the existing selected reader. Doctor
does not apply RBAC changes. For a known fixed Pod list, `resourceNames` can narrow
the log subresource further (the administrator maintains it as Pods change):

```yaml
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get"]
  # resourceNames: ["user-selected-pod"]
```

Pod metadata uses `get`/`list` (explicit `--pod` can avoid namespace inventory).
Logs may contain application/business information; a read-only API does not make
the content safe to disclose. A 403 remains an incomplete export, not permission
to use admin, Secrets, exec/cp, or another namespace.

## Milvus metadata

Optional PyMilvus calls are limited to server version, collection list/description,
collection statistics, load state and index list/description, with a bounded
collection count. Have an administrator grant the corresponding read-only
metadata privileges for the selected database/collections in that server version.
Do not assume a privilege group is identical between Milvus versions. A failed
metadata read must not cause a request for root/admin access. No Query/Search
privilege or access to row/vector payloads is needed by the collector.

Provide credentials through a local environment variable named
`MILVUS_DOCTOR_TOKEN` (default), or select another with `--token-env VARIABLE_NAME`.
Doctor never sets credentials, reads auth files, or prints token values. The user
can also choose unauthenticated access where their explicit test target permits it.

## What the safety boundary does and does not prove

Doctor's fixed subprocess allowlist rejects mutations, shells, exec, arbitrary
programs and unrestricted Kubernetes resources. Its SDK collector calls only
listed metadata methods. Credentials and API RBAC enforce the target-side limit.
A Skill's prose cannot revoke an AI host's other tools or pre-existing privileges;
do not present it as an isolation boundary for the entire host assistant.

Docker inspect/statistics access is read-only in Doctor, but normal Docker daemon
access is not a least-privilege account. Use an administrator-prepared snapshot
or an approved read-only proxy if the organization cannot grant daemon access.
