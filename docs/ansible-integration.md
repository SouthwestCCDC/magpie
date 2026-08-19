# Ansible Integration Guide

Download Magpie artifacts using `ansible.builtin.get_url` with Bearer tokens.

## Setup

Store token in Ansible Vault:
```bash
ansible-vault create group_vars/all/vault.yml
```

Add to vault:
```yaml
vault_magpie_token: "mgp_your_token_here"
```

## Examples

**Basic download by tag:**
```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/images/ubuntu/latest"
    dest: /opt/images/ubuntu
    mode: "0755"
    headers:
      Authorization: "Bearer {{ vault_magpie_token }}"
```

**By version tag:** Change `latest` to `v2.1` or `stable`

**By hash (immutable):** Use `/blobs/a1b2c3d4e5f67890` instead of tag

**Download and extract:**
```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/configs/nginx/latest"
    dest: /tmp/nginx.tar.gz
    headers:
      Authorization: "Bearer {{ vault_magpie_token }}"

- ansible.builtin.unarchive:
    src: /tmp/nginx.tar.gz
    dest: /etc/nginx/
    remote_src: true
```

**Restart service on change:**
```yaml
- ansible.builtin.get_url:
    url: "https://magpie.example.com/artifacts/apps/myapp/stable"
    dest: /opt/myapp/bin/myapp
    mode: "0755"
    force: true
    headers:
      Authorization: "Bearer {{ vault_magpie_token }}"
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
magpie_token: ""
magpie_artifact_path: ""
magpie_artifact_tag: "latest"
magpie_dest: ""
magpie_mode: "0644"
```

**roles/magpie_artifact/tasks/main.yml:**
```yaml
- ansible.builtin.get_url:
    url: "{{ magpie_server }}/artifacts/{{ magpie_artifact_path }}/{{ magpie_artifact_tag }}"
    dest: "{{ magpie_dest }}"
    mode: "{{ magpie_mode }}"
    headers:
      Authorization: "Bearer {{ magpie_token }}"
```

## Security Best Practices

- Use Ansible Vault for tokens (never plaintext in YAML)
- Use `no_log: true` on sensitive tasks
- Use hash refs for immutable artifact verification
- Use read-only tokens when possible
- Rotate tokens quarterly

---

*(AI-generated via Claude Code w/ Sonnet 4.5)*
