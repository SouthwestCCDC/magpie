# Ansible Integration Guide

Download Magpie artifacts in Ansible playbooks using `ansible.builtin.get_url` with Bearer token authentication.

## Prerequisites

- Magpie server accessible from Ansible control node
- Bearer token (required for artifact operations; `/artifacts/public/*` needs no token)
- Ansible Vault for token storage (recommended)

## Download Examples

**Latest version:**

```yaml
- name: Download artifact
  ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/scoring/engine/latest"
    dest: /opt/scoring/engine
    mode: "0755"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

**Specific tag:**

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/configs/app/v2.1"
    dest: /etc/app/config.tar.gz
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

**Hash reference (immutable):**

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/tools/validator/blobs/a1b2c3d4"
    dest: /usr/local/bin/validator
    mode: "0755"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

## Token Storage

Tokens are required for artifact downloads (except from `/artifacts/public/*`), uploads, and tag management.

**Never store tokens in plaintext.** Use Ansible Vault:

```bash
ansible-vault create group_vars/all/vault.yml
```

Add to vault file:

```yaml
vault_magpie_token: "mgp_your_token_here"
```

## Integrity Verification

Use hash references for immutable downloads:

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/scoring/engine/blobs/a1b2c3d4"
    dest: /opt/scoring/engine
    mode: "0755"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

Or verify with pre-defined checksums:

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/scoring/engine/stable"
    dest: /opt/scoring/engine
    checksum: "sha256:abc123def456..."
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

## Common Patterns

**Deploy scripts:**

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/scripts/setup-network/latest"
    dest: /usr/local/bin/setup-network
    mode: "0755"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

**Deploy and extract archives:**

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/configs/nginx/latest"
    dest: /tmp/nginx-config.tar.gz
    headers:
      Authorization: "Bearer {{ magpie_token }}"

- ansible.builtin.unarchive:
    src: /tmp/nginx-config.tar.gz
    dest: /etc/nginx/
    remote_src: true
```

**Restart service if binary changed:**

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/apps/myapp/stable"
    dest: /opt/myapp/bin/myapp
    mode: "0755"
    force: true
    headers:
      Authorization: "Bearer {{ magpie_token }}"
  register: download_result

- ansible.builtin.systemd:
    name: myapp
    state: restarted
  when: download_result.changed
```

## Reusable Role

**roles/magpie_artifact/defaults/main.yml:**

```yaml
magpie_server: "https://magpie.example.com"
magpie_token: ""  # Set via vault_magpie_token
magpie_artifact_path: ""
magpie_artifact_tag: "latest"
magpie_dest: ""
magpie_mode: "0644"
```

**roles/magpie_artifact/tasks/main.yml:**

```yaml
- ansible.builtin.assert:
    that:
      - magpie_artifact_path | length > 0
      - magpie_dest | length > 0

- ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/{{ magpie_artifact_path }}/{{ magpie_artifact_tag }}"
    dest: "{{ magpie_dest }}"
    mode: "{{ magpie_mode }}"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

**Use in playbook:**

```yaml
- hosts: scoring_servers
  roles:
    - role: magpie_artifact
      vars:
        magpie_artifact_path: "scoring/engine"
        magpie_artifact_tag: "stable"
        magpie_dest: /opt/scoring/engine
        magpie_mode: "0755"
```

## Troubleshooting

**401 Unauthorized**: Verify bearer token is correct. Files in `/artifacts/public/*` don't require auth.

**404 Not Found**: Verify artifact path and tag exist.

**Connection Timeout**: Increase timeout for large artifacts:

```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/images/large-vm/latest"
    dest: /var/lib/images/vm.qcow2
    timeout: 600
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

**Checksum Mismatch**: Use hash refs instead of mutable tags for guaranteed consistency.

## Security Best Practices

- **Use minimal token scope**: use `read` scope for artifact downloads (or `admin`/`write` when required); `/artifacts/public/*` needs no token; use `write` for uploads and `admin` only for administration
- **Vault-encrypt tokens**: never store plaintext
- **Prevent log exposure**: Use `no_log: true` in tasks with tokens
- **Pin versions**: Use specific tags or hash refs instead of `latest`
- **Verify integrity**: Use hash refs for immutable verification
- **Rotate tokens**: Update vault-stored tokens periodically

## See Also

- [User Guide](user-guide.md) - CLI and server administration
- [Authentik Integration](authentik-setup.md) - SSO for browser access
