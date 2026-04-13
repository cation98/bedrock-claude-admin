# Lane B Recovery Log

**복구 시작**: 2026-04-12
**사고 원인**: terraform apply drift 강행으로 Redis + OIDC provider 삭제
**EKS 클러스터**: bedrock-claude-eks (ACTIVE)
**EFS ID**: fs-0a2b5924041425002

---

## Step 1: terraform import

### Mount Target 조회 결과
- ap-northeast-2a: fsmt-0ab5fda69e3842e46 (subnet-05dfacf9e2a4de663)
- ap-northeast-2c: fsmt-0c5007a1d4644ff8c (subnet-0f6a5607cb20ce219)

### import 1: aws_ecr_repository.bedrock_ag
[0m[1maws_ecr_repository.bedrock_ag: Importing from ID "bedrock-claude/bedrock-access-gateway"...[0m
[0m[1m[32maws_ecr_repository.bedrock_ag: Import prepared![0m
[0m[32m  Prepared aws_ecr_repository for import[0m
[0m[1maws_ecr_repository.bedrock_ag: Refreshing state... [id=bedrock-claude/bedrock-access-gateway][0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_elasticache_cluster.redis: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348][0m
[31m[31m╷[0m[0m
[31m│[0m [0m[1m[31mError: [0m[0m[1mno matching ElastiCache Cluster found[0m
[31m│[0m [0m
[31m│[0m [0m[0m  with data.aws_elasticache_cluster.redis,
[31m│[0m [0m  on /Users/cation98/Project/bedrock-ai-agent/infra/terraform/elasticache.tf line 112, in data "aws_elasticache_cluster" "redis":
[31m│[0m [0m 112: data "aws_elasticache_cluster" "redis" [4m{[0m[0m
[31m│[0m [0m
[31m╵[0m[0m
[0m[0m
EXIT_CODE:0
### import 2: aws_efs_mount_target.eks_private[0] (2a / fsmt-0ab5fda69e3842e46)
[0m[1mdata.aws_elasticache_cluster.redis: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 1s [id=vpc-075deed66fcc7f348][0m
[0m[1maws_efs_mount_target.eks_private[0]: Importing from ID "fsmt-0ab5fda69e3842e46"...[0m
[0m[1m[32maws_efs_mount_target.eks_private[0]: Import prepared![0m
[0m[32m  Prepared aws_efs_mount_target for import[0m
[0m[1maws_efs_mount_target.eks_private[0]: Refreshing state... [id=fsmt-0ab5fda69e3842e46][0m
[31m[31m╷[0m[0m
[31m│[0m [0m[1m[31mError: [0m[0m[1mno matching ElastiCache Cluster found[0m
[31m│[0m [0m
[31m│[0m [0m[0m  with data.aws_elasticache_cluster.redis,
[31m│[0m [0m  on /Users/cation98/Project/bedrock-ai-agent/infra/terraform/elasticache.tf line 112, in data "aws_elasticache_cluster" "redis":
[31m│[0m [0m 112: data "aws_elasticache_cluster" "redis" [4m{[0m[0m
[31m│[0m [0m
[31m╵[0m[0m
[0m[0m
EXIT_CODE:0
### import 3: aws_efs_mount_target.eks_private[1] (2c / fsmt-0c5007a1d4644ff8c)
[0m[1mdata.aws_elasticache_cluster.redis: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348][0m
[0m[1maws_efs_mount_target.eks_private[1]: Importing from ID "fsmt-0c5007a1d4644ff8c"...[0m
[0m[1m[32maws_efs_mount_target.eks_private[1]: Import prepared![0m
[0m[32m  Prepared aws_efs_mount_target for import[0m
[0m[1maws_efs_mount_target.eks_private[1]: Refreshing state... [id=fsmt-0c5007a1d4644ff8c][0m
[31m[31m╷[0m[0m
[31m│[0m [0m[1m[31mError: [0m[0m[1mno matching ElastiCache Cluster found[0m
[31m│[0m [0m
[31m│[0m [0m[0m  with data.aws_elasticache_cluster.redis,
[31m│[0m [0m  on /Users/cation98/Project/bedrock-ai-agent/infra/terraform/elasticache.tf line 112, in data "aws_elasticache_cluster" "redis":
[31m│[0m [0m 112: data "aws_elasticache_cluster" "redis" [4m{[0m[0m
[31m│[0m [0m
[31m╵[0m[0m
[0m[0m
EXIT_CODE:0
### import 4: aws_iam_role.auth_gateway_bedrock
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_elasticache_cluster.redis: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1maws_iam_role.auth_gateway_bedrock: Importing from ID "bedrock-claude-auth-gateway-bedrock"...[0m
[0m[1m[32maws_iam_role.auth_gateway_bedrock: Import prepared![0m
[0m[32m  Prepared aws_iam_role for import[0m
[0m[1maws_iam_role.auth_gateway_bedrock: Refreshing state... [id=bedrock-claude-auth-gateway-bedrock][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 1s [id=vpc-075deed66fcc7f348][0m
[31m[31m╷[0m[0m
[31m│[0m [0m[1m[31mError: [0m[0m[1mno matching ElastiCache Cluster found[0m
[31m│[0m [0m
[31m│[0m [0m[0m  with data.aws_elasticache_cluster.redis,
[31m│[0m [0m  on /Users/cation98/Project/bedrock-ai-agent/infra/terraform/elasticache.tf line 112, in data "aws_elasticache_cluster" "redis":
[31m│[0m [0m 112: data "aws_elasticache_cluster" "redis" [4m{[0m[0m
[31m│[0m [0m
[31m╵[0m[0m
[0m[0m
EXIT_CODE:0
### data source state 제거
Removed data.aws_elasticache_cluster.redis
Successfully removed 1 resource instance(s).
EXIT_CODE:0
### import 재시도: aws_ecr_repository.bedrock_ag
[0m[1maws_ecr_repository.bedrock_ag: Importing from ID "bedrock-claude/bedrock-access-gateway"...[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1m[32maws_ecr_repository.bedrock_ag: Import prepared![0m
[0m[32m  Prepared aws_ecr_repository for import[0m
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1maws_ecr_repository.bedrock_ag: Refreshing state... [id=bedrock-claude/bedrock-access-gateway][0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348][0m
[0m[32m
Import successful!

The resources that were imported are shown above. These resources are now in
your Terraform state and will henceforth be managed by Terraform.
[0m
EXIT_CODE:0
### import: aws_efs_mount_target[0] + [1]
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 1s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 1s [id=vpc-075deed66fcc7f348][0m
[0m[1maws_efs_mount_target.eks_private[0]: Importing from ID "fsmt-0ab5fda69e3842e46"...[0m
[0m[1m[32maws_efs_mount_target.eks_private[0]: Import prepared![0m
[0m[32m  Prepared aws_efs_mount_target for import[0m
[0m[1maws_efs_mount_target.eks_private[0]: Refreshing state... [id=fsmt-0ab5fda69e3842e46][0m
[0m[32m
Import successful!

The resources that were imported are shown above. These resources are now in
your Terraform state and will henceforth be managed by Terraform.
[0m
EXIT_CODE[0]:0
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348][0m
[0m[1maws_efs_mount_target.eks_private[1]: Importing from ID "fsmt-0c5007a1d4644ff8c"...[0m
[0m[1m[32maws_efs_mount_target.eks_private[1]: Import prepared![0m
[0m[32m  Prepared aws_efs_mount_target for import[0m
[0m[1maws_efs_mount_target.eks_private[1]: Refreshing state... [id=fsmt-0c5007a1d4644ff8c][0m
[0m[32m
Import successful!

The resources that were imported are shown above. These resources are now in
your Terraform state and will henceforth be managed by Terraform.
[0m
EXIT_CODE[1]:0
### import: aws_iam_role.auth_gateway_bedrock
[0m[1mdata.aws_route_table.private: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Reading...[0m[0m
[0m[1mdata.aws_vpc.sko: Reading...[0m[0m
[0m[1mdata.tls_certificate.eks: Reading...[0m[0m
[0m[1mdata.aws_caller_identity.current: Read complete after 0s [id=680877507363][0m
[0m[1mdata.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f][0m
[0m[1maws_iam_role.auth_gateway_bedrock: Importing from ID "bedrock-claude-auth-gateway-bedrock"...[0m
[0m[1m[32maws_iam_role.auth_gateway_bedrock: Import prepared![0m
[0m[32m  Prepared aws_iam_role for import[0m
[0m[1maws_iam_role.auth_gateway_bedrock: Refreshing state... [id=bedrock-claude-auth-gateway-bedrock][0m
[0m[1mdata.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a][0m
[0m[1mdata.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348][0m
[0m[32m
Import successful!

The resources that were imported are shown above. These resources are now in
your Terraform state and will henceforth be managed by Terraform.
[0m
EXIT_CODE:0

## Step 2: terraform plan
data.aws_route_table.private: Reading...
data.aws_vpc.sko: Reading...
data.aws_caller_identity.current: Reading...
aws_ecr_repository.claude_terminal: Refreshing state... [id=bedrock-claude/claude-code-terminal]
aws_ecr_repository.bedrock_ag: Refreshing state... [id=bedrock-claude/bedrock-access-gateway]
aws_iam_role.eks_nodes: Refreshing state... [id=bedrock-claude-eks-node-role]
aws_iam_role.eks_cluster: Refreshing state... [id=bedrock-claude-eks-cluster-role]
aws_efs_file_system.user_workspaces: Refreshing state... [id=fs-0a2b5924041425002]
data.aws_caller_identity.current: Read complete after 0s [id=680877507363]
aws_kms_key.s3_vault: Refreshing state... [id=bc47d786-64b9-42ae-8d03-58374253dd23]
aws_s3_bucket.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
aws_kms_alias.s3_vault: Refreshing state... [id=alias/bedrock-claude-s3-vault]
aws_ecr_lifecycle_policy.claude_terminal: Refreshing state... [id=bedrock-claude/claude-code-terminal]
data.aws_route_table.private: Read complete after 0s [id=rtb-0700167f652e4360a]
aws_s3_bucket_versioning.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
aws_s3_bucket_public_access_block.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
aws_s3_bucket_server_side_encryption_configuration.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
aws_s3_bucket_policy.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
data.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348]
aws_subnet.eks_private[0]: Refreshing state... [id=subnet-05dfacf9e2a4de663]
aws_subnet.eks_private[1]: Refreshing state... [id=subnet-0f6a5607cb20ce219]
aws_security_group.redis: Refreshing state... [id=sg-004f7ce53bb5b5b06]
aws_security_group.efs: Refreshing state... [id=sg-006ff3ae39ba8d4f8]
aws_route_table_association.eks_private[0]: Refreshing state... [id=rtbassoc-04c4988be631c35e7]
aws_elasticache_subnet_group.redis: Refreshing state... [id=bedrock-claude-redis-subnet]
aws_route_table_association.eks_private[1]: Refreshing state... [id=rtbassoc-0d56243eaee39bcd0]
aws_efs_mount_target.eks_private[0]: Refreshing state... [id=fsmt-0ab5fda69e3842e46]
aws_efs_mount_target.eks_private[1]: Refreshing state... [id=fsmt-0c5007a1d4644ff8c]
aws_iam_role_policy_attachment.eks_cluster_policy: Refreshing state... [id=bedrock-claude-eks-cluster-role-20260321144540305800000004]
aws_eks_cluster.main: Refreshing state... [id=bedrock-claude-eks]
aws_s3_bucket_lifecycle_configuration.vault: Refreshing state... [id=bedrock-claude-s3-vault-680877507363]
aws_iam_role_policy_attachment.eks_container_registry: Refreshing state... [id=bedrock-claude-eks-node-role-20260321144540001100000002]
aws_iam_role_policy_attachment.eks_cni_policy: Refreshing state... [id=bedrock-claude-eks-node-role-20260321144539941200000001]
aws_iam_role_policy_attachment.eks_worker_node_policy: Refreshing state... [id=bedrock-claude-eks-node-role-20260321144540204900000003]
aws_eks_node_group.main: Refreshing state... [id=bedrock-claude-eks:bedrock-claude-nodes]
aws_eks_node_group.dedicated: Refreshing state... [id=bedrock-claude-eks:bedrock-claude-dedicated-nodes]
aws_iam_role.auth_gateway_bedrock: Refreshing state... [id=bedrock-claude-auth-gateway-bedrock]
aws_iam_role.cluster_autoscaler: Refreshing state... [id=bedrock-claude-cluster-autoscaler]
aws_iam_role.bedrock_access: Refreshing state... [id=bedrock-claude-bedrock-access]
aws_iam_role_policy.cluster_autoscaler: Refreshing state... [id=bedrock-claude-cluster-autoscaler:bedrock-claude-cluster-autoscaler]
aws_iam_role_policy.bedrock_invoke: Refreshing state... [id=bedrock-claude-bedrock-access:bedrock-claude-bedrock-invoke]

Terraform used the selected providers to generate the following execution
plan. Resource actions are indicated with the following symbols:
  + create
  ~ update in-place
-/+ destroy and then create replacement
 <= read (data resources)

Terraform will perform the following actions:

  # data.tls_certificate.eks will be read during apply
  # (config refers to values not yet known)
 <= data "tls_certificate" "eks" {
      + certificates = (known after apply)
      + id           = (known after apply)
      + url          = (known after apply)
    }

  # aws_ecr_lifecycle_policy.bedrock_ag will be created
  + resource "aws_ecr_lifecycle_policy" "bedrock_ag" {
      + id          = (known after apply)
      + policy      = jsonencode(
            {
              + rules = [
                  + {
                      + action       = {
                          + type = "expire"
                        }
                      + description  = "Keep last 10 images"
                      + rulePriority = 1
                      + selection    = {
                          + countNumber = 10
                          + countType   = "imageCountMoreThan"
                          + tagStatus   = "any"
                        }
                    },
                ]
            }
        )
      + registry_id = (known after apply)
      + repository  = "bedrock-claude/bedrock-access-gateway"
    }

  # aws_eks_cluster.main must be replaced
-/+ resource "aws_eks_cluster" "main" {
      ~ arn                           = "arn:aws:eks:ap-northeast-2:680877507363:cluster/bedrock-claude-eks" -> (known after apply)
      ~ bootstrap_self_managed_addons = true -> false # forces replacement
      ~ certificate_authority         = [
          - {
              - data = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSURCVENDQWUyZ0F3SUJBZ0lJU1RjaWpRaENtNnd3RFFZSktvWklodmNOQVFFTEJRQXdGVEVUTUJFR0ExVUUKQXhNS2EzVmlaWEp1WlhSbGN6QWVGdzB5TmpBek1qRXhORFExTXpSYUZ3MHpOakF6TVRneE5EVXdNelJhTUJVeApFekFSQmdOVkJBTVRDbXQxWW1WeWJtVjBaWE13Z2dFaU1BMEdDU3FHU0liM0RRRUJBUVVBQTRJQkR3QXdnZ0VLCkFvSUJBUUN4d3RjRDBjSm9VVUVwQWF1VHgwdTZ2di9JblRGNjlmY05FNXJ6U0RzNTF0ZkxReEEvRXBnVGxMdlcKMXVOM3k1Tk1tdHdnSVBnMFY4dlV5UHdKcnVVNk5uL0o2eEw2Rk1DZ2lWMmt6b1ZUWDNzSzBQVkw2NkVlT2ZXLwpkbldoWGVBaEJXZi8vM1duZ25DMHU5aFJiSHhIWVZXdm5IOVRqQ3ZFMTUzdHhzOXJYOVZMakhYUGRaRUNxeXhhCllqTFg3MXN5ZXl0WWxISitjTVFmais5TWxXRWhseFBjNjhUU1A3V0dZa2NvQXRnVHRveUZHMkZ6QXhXWFp3dkQKcDNjZTRSa2JKOGo3WHg3RTFreXVMYlkzclJQbkx3TEI1eU5hcU5QOUczeXRHUVlKZkJhajEyMmhRN3psek9GRQoyMjFNWWR5OFZVbHRQOC9VSmVjQ0RmWHZMQk90QWdNQkFBR2pXVEJYTUE0R0ExVWREd0VCL3dRRUF3SUNwREFQCkJnTlZIUk1CQWY4RUJUQURBUUgvTUIwR0ExVWREZ1FXQkJUVGp1blFtYTdMbTY1bnVrdFJwVXhUeGFCY1VEQVYKQmdOVkhSRUVEakFNZ2dwcmRXSmxjbTVsZEdWek1BMEdDU3FHU0liM0RRRUJDd1VBQTRJQkFRQkFGYUYya25RKwozSW5mODNudDlva2tMNFp3SE1JbHJSdURpU3FaNHcxTy9kN0RtU3huZ3dvVndVUFBxLzg5VzMvNG5vUXRQcCthCjF0N0ZoR3lhc0pYK1JMTnB3d2sycExQUTd1bW8zZC9XUWErN1M3d212eVhYc3BYWjlDSmJla0hWVTlqMHQyamwKMHJOR3hGcE8zK3d4VGhiUmFsME1ySi9RbGtoSEE0c3NsU0JJRC9UZVlkNURwNFF0R3FRMXdpTjRXRGMxY2dnLwpUTXpzSFZyWkdpKzhJK1doTy83WHdSTzY4Y1phMU92SWZDei9kbkVXUmMyVDNJQmkvVHRVWnp5NkpZMjlnNzhRCndnOVovRm5KSG05R1RuZERUUTVJRHdqVi9Zd0lHMyt4Wk4yOGIrUzBaeDJnZXR3eTM5NEVFVmtiOWpCRTZMemgKRWw5ZDY1NXhWV1g4Ci0tLS0tRU5EIENFUlRJRklDQVRFLS0tLS0K"
            },
        ] -> (known after apply)
      + cluster_id                    = (known after apply)
      ~ created_at                    = "2026-03-21T14:45:42Z" -> (known after apply)
      - enabled_cluster_log_types     = [] -> null
      ~ endpoint                      = "https://77AD470D1F122F9E7322B2E662EA42DF.gr7.ap-northeast-2.eks.amazonaws.com" -> (known after apply)
      ~ id                            = "bedrock-claude-eks" -> (known after apply)
      ~ identity                      = [
          - {
              - oidc = [
                  - {
                      - issuer = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
                    },
                ]
            },
        ] -> (known after apply)
        name                          = "bedrock-claude-eks"
      ~ platform_version              = "eks.56" -> (known after apply)
      ~ status                        = "ACTIVE" -> (known after apply)
      - tags                          = {} -> null
        # (3 unchanged attributes hidden)

      ~ access_config {
          ~ bootstrap_cluster_creator_admin_permissions = false -> true # forces replacement
            # (1 unchanged attribute hidden)
        }

      - kubernetes_network_config {
          - ip_family         = "ipv4" -> null
          - service_ipv4_cidr = "172.20.0.0/16" -> null

          - elastic_load_balancing {
              - enabled = false -> null
            }
        }

      - upgrade_policy {
          - support_type = "EXTENDED" -> null
        }

      ~ vpc_config {
          ~ cluster_security_group_id = "sg-08af31a0f5282c702" -> (known after apply)
          ~ public_access_cidrs       = [
              - "0.0.0.0/0",
            ] -> (known after apply)
          - security_group_ids        = [] -> null
          ~ vpc_id                    = "vpc-075deed66fcc7f348" -> (known after apply)
            # (3 unchanged attributes hidden)
        }
    }

  # aws_eks_node_group.ingress will be created
  + resource "aws_eks_node_group" "ingress" {
      + ami_type               = (known after apply)
      + arn                    = (known after apply)
      + capacity_type          = (known after apply)
      + cluster_name           = "bedrock-claude-eks"
      + disk_size              = (known after apply)
      + id                     = (known after apply)
      + instance_types         = [
          + "t3.large",
        ]
      + labels                 = {
          + "role" = "ingress"
        }
      + node_group_name        = "ingress-workers"
      + node_group_name_prefix = (known after apply)
      + node_role_arn          = "arn:aws:iam::680877507363:role/bedrock-claude-eks-node-role"
      + release_version        = (known after apply)
      + resources              = (known after apply)
      + status                 = (known after apply)
      + subnet_ids             = [
          + "subnet-05dfacf9e2a4de663",
          + "subnet-0f6a5607cb20ce219",
        ]
      + tags                   = {
          + "Env"                                          = "prod"
          + "Name"                                         = "ingress-workers"
          + "Owner"                                        = "N1102359"
          + "Service"                                      = "sko-claude-ai-agent"
          + "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
          + "k8s.io/cluster-autoscaler/enabled"            = "true"
        }
      + tags_all               = {
          + "Env"                                          = "prod"
          + "Environment"                                  = "prod"
          + "ManagedBy"                                    = "terraform"
          + "Name"                                         = "ingress-workers"
          + "Owner"                                        = "N1102359"
          + "Project"                                      = "bedrock-ai-agent"
          + "Service"                                      = "sko-claude-ai-agent"
          + "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
          + "k8s.io/cluster-autoscaler/enabled"            = "true"
        }
      + version                = (known after apply)

      + scaling_config {
          + desired_size = 2
          + max_size     = 6
          + min_size     = 2
        }

      + taint {
          + effect = "NO_SCHEDULE"
          + key    = "dedicated"
          + value  = "ingress"
        }

      + update_config {
          + max_unavailable = 1
        }
    }

  # aws_eks_node_group.main will be updated in-place
  ~ resource "aws_eks_node_group" "main" {
        id              = "bedrock-claude-eks:bedrock-claude-nodes"
        tags            = {
            "Env"                                          = "prod"
            "Owner"                                        = "N1102359"
            "Service"                                      = "sko-claude-ai-agent"
            "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
            "k8s.io/cluster-autoscaler/enabled"            = "true"
        }
        # (15 unchanged attributes hidden)

      ~ scaling_config {
          ~ desired_size = 2 -> 0
            # (2 unchanged attributes hidden)
        }

        # (1 unchanged block hidden)
    }

  # aws_eks_node_group.system will be created
  + resource "aws_eks_node_group" "system" {
      + ami_type               = (known after apply)
      + arn                    = (known after apply)
      + capacity_type          = (known after apply)
      + cluster_name           = "bedrock-claude-eks"
      + disk_size              = (known after apply)
      + id                     = (known after apply)
      + instance_types         = [
          + "t3.large",
        ]
      + labels                 = {
          + "role" = "system"
        }
      + node_group_name        = "system-node-large"
      + node_group_name_prefix = (known after apply)
      + node_role_arn          = "arn:aws:iam::680877507363:role/bedrock-claude-eks-node-role"
      + release_version        = (known after apply)
      + resources              = (known after apply)
      + status                 = (known after apply)
      + subnet_ids             = [
          + "subnet-05dfacf9e2a4de663",
          + "subnet-0f6a5607cb20ce219",
        ]
      + tags                   = {
          + "Env"                                          = "prod"
          + "Name"                                         = "system-node-large"
          + "Owner"                                        = "N1102359"
          + "Service"                                      = "sko-claude-ai-agent"
          + "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
          + "k8s.io/cluster-autoscaler/enabled"            = "true"
        }
      + tags_all               = {
          + "Env"                                          = "prod"
          + "Environment"                                  = "prod"
          + "ManagedBy"                                    = "terraform"
          + "Name"                                         = "system-node-large"
          + "Owner"                                        = "N1102359"
          + "Project"                                      = "bedrock-ai-agent"
          + "Service"                                      = "sko-claude-ai-agent"
          + "k8s.io/cluster-autoscaler/bedrock-claude-eks" = "owned"
          + "k8s.io/cluster-autoscaler/enabled"            = "true"
        }
      + version                = (known after apply)

      + scaling_config {
          + desired_size = 2
          + max_size     = 3
          + min_size     = 2
        }

      + taint {
          + effect = "NO_SCHEDULE"
          + key    = "dedicated"
          + value  = "system"
        }

      + update_config {
          + max_unavailable = 1
        }
    }

  # aws_elasticache_cluster.redis will be created
  + resource "aws_elasticache_cluster" "redis" {
      + apply_immediately          = (known after apply)
      + arn                        = (known after apply)
      + auto_minor_version_upgrade = "true"
      + availability_zone          = (known after apply)
      + az_mode                    = (known after apply)
      + cache_nodes                = (known after apply)
      + cluster_address            = (known after apply)
      + cluster_id                 = "bedrock-claude-redis"
      + configuration_endpoint     = (known after apply)
      + engine                     = "redis"
      + engine_version             = "7.1"
      + engine_version_actual      = (known after apply)
      + id                         = (known after apply)
      + ip_discovery               = (known after apply)
      + maintenance_window         = (known after apply)
      + network_type               = (known after apply)
      + node_type                  = "cache.t3.micro"
      + num_cache_nodes            = 1
      + parameter_group_name       = "default.redis7"
      + port                       = 6379
      + preferred_outpost_arn      = (known after apply)
      + replication_group_id       = (known after apply)
      + security_group_ids         = [
          + "sg-004f7ce53bb5b5b06",
        ]
      + snapshot_window            = (known after apply)
      + subnet_group_name          = "bedrock-claude-redis-subnet"
      + tags                       = {
          + "Env"     = "prod"
          + "Name"    = "bedrock-claude-redis"
          + "Owner"   = "N1102359"
          + "Service" = "sko-claude-ai-agent"
        }
      + tags_all                   = {
          + "Env"         = "prod"
          + "Environment" = "prod"
          + "ManagedBy"   = "terraform"
          + "Name"        = "bedrock-claude-redis"
          + "Owner"       = "N1102359"
          + "Project"     = "bedrock-ai-agent"
          + "Service"     = "sko-claude-ai-agent"
        }
      + transit_encryption_enabled = (known after apply)
    }

  # aws_iam_openid_connect_provider.eks will be created
  + resource "aws_iam_openid_connect_provider" "eks" {
      + arn             = (known after apply)
      + client_id_list  = [
          + "sts.amazonaws.com",
        ]
      + id              = (known after apply)
      + tags_all        = {
          + "Environment" = "prod"
          + "ManagedBy"   = "terraform"
          + "Project"     = "bedrock-ai-agent"
        }
      + thumbprint_list = (known after apply)
      + url             = (known after apply)
    }

  # aws_iam_role.auth_gateway_bedrock will be updated in-place
  ~ resource "aws_iam_role" "auth_gateway_bedrock" {
      ~ assume_role_policy    = jsonencode(
            {
              - Statement = [
                  - {
                      - Action    = "sts:AssumeRoleWithWebIdentity"
                      - Condition = {
                          - StringEquals = {
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:aud" = "sts.amazonaws.com"
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:sub" = "system:serviceaccount:platform:platform-admin-sa"
                            }
                        }
                      - Effect    = "Allow"
                      - Principal = {
                          - Federated = "arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
                        }
                    },
                ]
              - Version   = "2012-10-17"
            }
        ) -> (known after apply)
        id                    = "bedrock-claude-auth-gateway-bedrock"
        name                  = "bedrock-claude-auth-gateway-bedrock"
        tags                  = {
            "Env"     = "prod"
            "Name"    = "bedrock-claude-auth-gateway-bedrock"
            "Owner"   = "N1102359"
            "Service" = "sko-claude-ai-agent"
        }
        # (8 unchanged attributes hidden)

        # (1 unchanged block hidden)
    }

  # aws_iam_role.bedrock_access will be updated in-place
  ~ resource "aws_iam_role" "bedrock_access" {
      ~ assume_role_policy    = jsonencode(
            {
              - Statement = [
                  - {
                      - Action    = "sts:AssumeRoleWithWebIdentity"
                      - Condition = {
                          - StringEquals = {
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:aud" = "sts.amazonaws.com"
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:sub" = "system:serviceaccount:claude-sessions:claude-terminal-sa"
                            }
                        }
                      - Effect    = "Allow"
                      - Principal = {
                          - Federated = "arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
                        }
                    },
                ]
              - Version   = "2012-10-17"
            }
        ) -> (known after apply)
        id                    = "bedrock-claude-bedrock-access"
        name                  = "bedrock-claude-bedrock-access"
        tags                  = {}
        # (8 unchanged attributes hidden)

        # (1 unchanged block hidden)
    }

  # aws_iam_role.bedrock_ag_access will be created
  + resource "aws_iam_role" "bedrock_ag_access" {
      + arn                   = (known after apply)
      + assume_role_policy    = (known after apply)
      + create_date           = (known after apply)
      + force_detach_policies = false
      + id                    = (known after apply)
      + managed_policy_arns   = (known after apply)
      + max_session_duration  = 3600
      + name                  = "bedrock-claude-bedrock-ag-access"
      + name_prefix           = (known after apply)
      + path                  = "/"
      + tags                  = {
          + "Env"     = "prod"
          + "Name"    = "bedrock-claude-bedrock-ag-access"
          + "Owner"   = "N1102359"
          + "Service" = "sko-claude-ai-agent"
        }
      + tags_all              = {
          + "Env"         = "prod"
          + "Environment" = "prod"
          + "ManagedBy"   = "terraform"
          + "Name"        = "bedrock-claude-bedrock-ag-access"
          + "Owner"       = "N1102359"
          + "Project"     = "bedrock-ai-agent"
          + "Service"     = "sko-claude-ai-agent"
        }
      + unique_id             = (known after apply)
    }

  # aws_iam_role.cluster_autoscaler will be updated in-place
  ~ resource "aws_iam_role" "cluster_autoscaler" {
      ~ assume_role_policy    = jsonencode(
            {
              - Statement = [
                  - {
                      - Action    = "sts:AssumeRoleWithWebIdentity"
                      - Condition = {
                          - StringEquals = {
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:aud" = "sts.amazonaws.com"
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:sub" = "system:serviceaccount:kube-system:cluster-autoscaler"
                            }
                        }
                      - Effect    = "Allow"
                      - Principal = {
                          - Federated = "arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
                        }
                    },
                ]
              - Version   = "2012-10-17"
            }
        ) -> (known after apply)
        id                    = "bedrock-claude-cluster-autoscaler"
        name                  = "bedrock-claude-cluster-autoscaler"
        tags                  = {}
        # (8 unchanged attributes hidden)

        # (1 unchanged block hidden)
    }

  # aws_iam_role_policy.auth_gateway_bedrock_invoke will be created
  + resource "aws_iam_role_policy" "auth_gateway_bedrock_invoke" {
      + id          = (known after apply)
      + name        = "bedrock-claude-auth-gateway-bedrock-invoke"
      + name_prefix = (known after apply)
      + policy      = jsonencode(
            {
              + Statement = [
                  + {
                      + Action   = [
                          + "bedrock:InvokeModel",
                          + "bedrock:InvokeModelWithResponseStream",
                          + "bedrock:Converse",
                          + "bedrock:ConverseStream",
                        ]
                      + Effect   = "Allow"
                      + Resource = [
                          + "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*",
                        ]
                      + Sid      = "AllowBedrockInvoke"
                    },
                  + {
                      + Action   = [
                          + "bedrock:ListFoundationModels",
                          + "bedrock:GetFoundationModel",
                        ]
                      + Effect   = "Allow"
                      + Resource = "*"
                      + Sid      = "AllowModelDiscovery"
                    },
                ]
              + Version   = "2012-10-17"
            }
        )
      + role        = "bedrock-claude-auth-gateway-bedrock"
    }

  # aws_iam_role_policy.bedrock_ag_invoke will be created
  + resource "aws_iam_role_policy" "bedrock_ag_invoke" {
      + id          = (known after apply)
      + name        = "bedrock-claude-bedrock-ag-invoke"
      + name_prefix = (known after apply)
      + policy      = jsonencode(
            {
              + Statement = [
                  + {
                      + Action   = [
                          + "bedrock:InvokeModel",
                          + "bedrock:InvokeModelWithResponseStream",
                        ]
                      + Effect   = "Allow"
                      + Resource = [
                          + "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*",
                        ]
                      + Sid      = "AllowBedrockInvoke"
                    },
                  + {
                      + Action   = [
                          + "bedrock:ListFoundationModels",
                          + "bedrock:GetFoundationModel",
                        ]
                      + Effect   = "Allow"
                      + Resource = "*"
                      + Sid      = "AllowModelDiscovery"
                    },
                ]
              + Version   = "2012-10-17"
            }
        )
      + role        = (known after apply)
    }

  # aws_security_group_rule.eks_to_efs will be created
  + resource "aws_security_group_rule" "eks_to_efs" {
      + description              = "EKS nodes to EFS NFS"
      + from_port                = 2049
      + id                       = (known after apply)
      + protocol                 = "tcp"
      + security_group_id        = (known after apply)
      + security_group_rule_id   = (known after apply)
      + self                     = false
      + source_security_group_id = "sg-006ff3ae39ba8d4f8"
      + to_port                  = 2049
      + type                     = "egress"
    }

  # aws_security_group_rule.eks_to_redis will be created
  + resource "aws_security_group_rule" "eks_to_redis" {
      + description              = "EKS nodes to ElastiCache Redis"
      + from_port                = 6379
      + id                       = (known after apply)
      + protocol                 = "tcp"
      + security_group_id        = (known after apply)
      + security_group_rule_id   = (known after apply)
      + self                     = false
      + source_security_group_id = "sg-004f7ce53bb5b5b06"
      + to_port                  = 6379
      + type                     = "egress"
    }

Plan: 11 to add, 4 to change, 1 to destroy.

Changes to Outputs:
  + bedrock_ag_role_arn           = (known after apply)
  ~ eks_cluster_endpoint          = "https://77AD470D1F122F9E7322B2E662EA42DF.gr7.ap-northeast-2.eks.amazonaws.com" -> (known after apply)
  + eks_oidc_provider_arn         = (known after apply)
  + redis_connection_url          = (known after apply)
  + redis_port                    = (known after apply)
  + redis_primary_endpoint        = (known after apply)
  + redis_reader_endpoint         = (known after apply)

Warning: Invalid Attribute Combination

  with aws_s3_bucket_lifecycle_configuration.vault,
  on s3-vault.tf line 126, in resource "aws_s3_bucket_lifecycle_configuration" "vault":
 126: resource "aws_s3_bucket_lifecycle_configuration" "vault" {

No attribute specified when one (and only one) of
[rule[0].filter,rule[0].prefix] is required

This will be an error in a future version of the provider

(and one more similar warning elsewhere)

─────────────────────────────────────────────────────────────────────────────

Note: You didn't use the -out option to save this plan, so Terraform can't
guarantee to take exactly these actions if you run "terraform apply" now.
## Step 2 plan 결과 — 중단
Plan: 11 to add, 4 to change, 1 to destroy
DANGER: aws_eks_cluster.main -/+ (destroy+replace) → 중단
DANGER: aws_eks_node_group.main desired_size 2→0 → 중단
apply 미실행. 보고 대기.

## Step 3 — eks.tf config alignment fix (2026-04-12 세션 재개)

**원인 파악**: eks.tf의 두 속성이 실제 AWS 값과 불일치
- `bootstrap_self_managed_addons`: 코드=`false`, AWS실제값=`true` → replacement 유발
- `bootstrap_cluster_creator_admin_permissions`: 코드=`true`, AWS실제값=`false` → replacement 유발

**수정**: eks.tf 코드를 실제 배포 값에 맞춤 (기능 변경 없음)
```hcl
bootstrap_self_managed_addons = true  # (was false)
access_config {
  bootstrap_cluster_creator_admin_permissions = false  # (was true)
}
```

## Step 4 — terraform plan 재실행 결과

**Plan: 10 to add, 5 to change, 0 to destroy** ✅

| 리소스 | 유형 | 안전성 |
|--------|------|--------|
| aws_elasticache_cluster.redis | + create | ✅ 복구 |
| aws_iam_openid_connect_provider.eks | + create | ✅ 복구 |
| aws_security_group_rule.eks_to_efs | + create | ✅ 복구 |
| aws_security_group_rule.eks_to_redis | + create | ✅ 복구 |
| aws_iam_role_policy.auth_gateway_bedrock_invoke | + create | ✅ Lane B |
| aws_eks_node_group.system | + create | ✅ Phase 0 신규 |
| aws_eks_node_group.ingress | + create | ✅ Phase 0 신규 |
| aws_iam_role.bedrock_ag_access + policy | + create | ✅ 신규 |
| aws_ecr_lifecycle_policy.bedrock_ag | + create | ✅ 신규 |
| aws_eks_node_group.main | ~ desired_size 2→0 | ⚠️ 스케일다운 |
| aws_eks_node_group.dedicated | ~ tag | ✅ 안전 |
| aws_iam_role.* ×3 | ~ assume_role_policy | ✅ OIDC ARN 갱신 |

팀장 보고 완료. apply 승인 대기.
data.aws_vpc.sko: Reading...
aws_iam_role.eks_cluster: Refreshing state... [id=bedrock-claude-eks-cluster-role]
data.aws_vpc.sko: Read complete after 0s [id=vpc-075deed66fcc7f348]
aws_subnet.eks_private[0]: Refreshing state... [id=subnet-05dfacf9e2a4de663]
aws_subnet.eks_private[1]: Refreshing state... [id=subnet-0f6a5607cb20ce219]
aws_security_group.redis: Refreshing state... [id=sg-004f7ce53bb5b5b06]
aws_security_group.efs: Refreshing state... [id=sg-006ff3ae39ba8d4f8]
aws_elasticache_subnet_group.redis: Refreshing state... [id=bedrock-claude-redis-subnet]
aws_iam_role_policy_attachment.eks_cluster_policy: Refreshing state... [id=bedrock-claude-eks-cluster-role-20260321144540305800000004]
aws_eks_cluster.main: Refreshing state... [id=bedrock-claude-eks]
data.tls_certificate.eks: Reading...
data.tls_certificate.eks: Read complete after 0s [id=db89cecc3adb9ea30f9819b0e895f1b1184c090f]
aws_iam_role.auth_gateway_bedrock: Refreshing state... [id=bedrock-claude-auth-gateway-bedrock]

Terraform used the selected providers to generate the following execution
plan. Resource actions are indicated with the following symbols:
  + create
  ~ update in-place

Terraform will perform the following actions:

  # aws_elasticache_cluster.redis will be created
  + resource "aws_elasticache_cluster" "redis" {
      + apply_immediately          = (known after apply)
      + arn                        = (known after apply)
      + auto_minor_version_upgrade = "true"
      + availability_zone          = (known after apply)
      + az_mode                    = (known after apply)
      + cache_nodes                = (known after apply)
      + cluster_address            = (known after apply)
      + cluster_id                 = "bedrock-claude-redis"
      + configuration_endpoint     = (known after apply)
      + engine                     = "redis"
      + engine_version             = "7.1"
      + engine_version_actual      = (known after apply)
      + id                         = (known after apply)
      + ip_discovery               = (known after apply)
      + maintenance_window         = (known after apply)
      + network_type               = (known after apply)
      + node_type                  = "cache.t3.micro"
      + num_cache_nodes            = 1
      + parameter_group_name       = "default.redis7"
      + port                       = 6379
      + preferred_outpost_arn      = (known after apply)
      + replication_group_id       = (known after apply)
      + security_group_ids         = [
          + "sg-004f7ce53bb5b5b06",
        ]
      + snapshot_window            = (known after apply)
      + subnet_group_name          = "bedrock-claude-redis-subnet"
      + tags                       = {
          + "Env"     = "prod"
          + "Name"    = "bedrock-claude-redis"
          + "Owner"   = "N1102359"
          + "Service" = "sko-claude-ai-agent"
        }
      + tags_all                   = {
          + "Env"         = "prod"
          + "Environment" = "prod"
          + "ManagedBy"   = "terraform"
          + "Name"        = "bedrock-claude-redis"
          + "Owner"       = "N1102359"
          + "Project"     = "bedrock-ai-agent"
          + "Service"     = "sko-claude-ai-agent"
        }
      + transit_encryption_enabled = (known after apply)
    }

  # aws_iam_openid_connect_provider.eks will be created
  + resource "aws_iam_openid_connect_provider" "eks" {
      + arn             = (known after apply)
      + client_id_list  = [
          + "sts.amazonaws.com",
        ]
      + id              = (known after apply)
      + tags_all        = {
          + "Environment" = "prod"
          + "ManagedBy"   = "terraform"
          + "Project"     = "bedrock-ai-agent"
        }
      + thumbprint_list = [
          + "06b25927c42a721631c1efd9431e648fa62e1e39",
        ]
      + url             = "https://oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
    }

  # aws_iam_role.auth_gateway_bedrock will be updated in-place
  ~ resource "aws_iam_role" "auth_gateway_bedrock" {
      ~ assume_role_policy    = jsonencode(
            {
              - Statement = [
                  - {
                      - Action    = "sts:AssumeRoleWithWebIdentity"
                      - Condition = {
                          - StringEquals = {
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:aud" = "sts.amazonaws.com"
                              - "oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF:sub" = "system:serviceaccount:platform:platform-admin-sa"
                            }
                        }
                      - Effect    = "Allow"
                      - Principal = {
                          - Federated = "arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
                        }
                    },
                ]
              - Version   = "2012-10-17"
            }
        ) -> (known after apply)
        id                    = "bedrock-claude-auth-gateway-bedrock"
        name                  = "bedrock-claude-auth-gateway-bedrock"
        tags                  = {
            "Env"     = "prod"
            "Name"    = "bedrock-claude-auth-gateway-bedrock"
            "Owner"   = "N1102359"
            "Service" = "sko-claude-ai-agent"
        }
        # (8 unchanged attributes hidden)

        # (1 unchanged block hidden)
    }

  # aws_iam_role_policy.auth_gateway_bedrock_invoke will be created
  + resource "aws_iam_role_policy" "auth_gateway_bedrock_invoke" {
      + id          = (known after apply)
      + name        = "bedrock-claude-auth-gateway-bedrock-invoke"
      + name_prefix = (known after apply)
      + policy      = jsonencode(
            {
              + Statement = [
                  + {
                      + Action   = [
                          + "bedrock:InvokeModel",
                          + "bedrock:InvokeModelWithResponseStream",
                          + "bedrock:Converse",
                          + "bedrock:ConverseStream",
                        ]
                      + Effect   = "Allow"
                      + Resource = [
                          + "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*:680877507363:inference-profile/global.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/us.anthropic.claude-*",
                          + "arn:aws:bedrock:*::inference-profile/global.anthropic.claude-*",
                        ]
                      + Sid      = "AllowBedrockInvoke"
                    },
                  + {
                      + Action   = [
                          + "bedrock:ListFoundationModels",
                          + "bedrock:GetFoundationModel",
                        ]
                      + Effect   = "Allow"
                      + Resource = "*"
                      + Sid      = "AllowModelDiscovery"
                    },
                ]
              + Version   = "2012-10-17"
            }
        )
      + role        = "bedrock-claude-auth-gateway-bedrock"
    }

  # aws_security_group_rule.eks_to_efs will be created
  + resource "aws_security_group_rule" "eks_to_efs" {
      + description              = "EKS nodes to EFS NFS"
      + from_port                = 2049
      + id                       = (known after apply)
      + protocol                 = "tcp"
      + security_group_id        = "sg-08af31a0f5282c702"
      + security_group_rule_id   = (known after apply)
      + self                     = false
      + source_security_group_id = "sg-006ff3ae39ba8d4f8"
      + to_port                  = 2049
      + type                     = "egress"
    }

  # aws_security_group_rule.eks_to_redis will be created
  + resource "aws_security_group_rule" "eks_to_redis" {
      + description              = "EKS nodes to ElastiCache Redis"
      + from_port                = 6379
      + id                       = (known after apply)
      + protocol                 = "tcp"
      + security_group_id        = "sg-08af31a0f5282c702"
      + security_group_rule_id   = (known after apply)
      + self                     = false
      + source_security_group_id = "sg-004f7ce53bb5b5b06"
      + to_port                  = 6379
      + type                     = "egress"
    }

Plan: 5 to add, 1 to change, 0 to destroy.

Changes to Outputs:
  + eks_oidc_provider_arn         = (known after apply)
  + redis_connection_url          = (known after apply)
  + redis_port                    = (known after apply)
  + redis_primary_endpoint        = (known after apply)
  + redis_reader_endpoint         = (known after apply)

Warning: Resource targeting is in effect

You are creating a plan with the -target option, which means that the result
of this plan may not represent all of the changes requested by the current
configuration.

The -target option is not for routine use, and is provided only for
exceptional situations such as recovering from errors or mistakes, or when
Terraform specifically suggests to use it as part of an error message.

Warning: Invalid Attribute Combination

  with aws_s3_bucket_lifecycle_configuration.vault,
  on s3-vault.tf line 126, in resource "aws_s3_bucket_lifecycle_configuration" "vault":
 126: resource "aws_s3_bucket_lifecycle_configuration" "vault" {

No attribute specified when one (and only one) of
[rule[0].filter,rule[0].prefix] is required

This will be an error in a future version of the provider

Do you want to perform these actions?
  Terraform will perform the actions described above.
  Only 'yes' will be accepted to approve.

  Enter a value: 
aws_iam_openid_connect_provider.eks: Creating...
aws_security_group_rule.eks_to_efs: Creating...
aws_security_group_rule.eks_to_redis: Creating...
aws_elasticache_cluster.redis: Creating...
aws_security_group_rule.eks_to_efs: Creation complete after 1s [id=sgrule-986966450]
aws_iam_openid_connect_provider.eks: Creation complete after 1s [id=arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF]
aws_iam_role_policy.auth_gateway_bedrock_invoke: Creating...
aws_security_group_rule.eks_to_redis: Creation complete after 1s [id=sgrule-4168961846]
aws_iam_role_policy.auth_gateway_bedrock_invoke: Creation complete after 1s [id=bedrock-claude-auth-gateway-bedrock:bedrock-claude-auth-gateway-bedrock-invoke]
aws_elasticache_cluster.redis: Still creating... [10s elapsed]
aws_elasticache_cluster.redis: Still creating... [20s elapsed]
aws_elasticache_cluster.redis: Still creating... [30s elapsed]
aws_elasticache_cluster.redis: Still creating... [40s elapsed]
aws_elasticache_cluster.redis: Still creating... [50s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m0s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m10s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m20s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m30s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m40s elapsed]
aws_elasticache_cluster.redis: Still creating... [1m50s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m0s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m10s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m20s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m30s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m40s elapsed]
aws_elasticache_cluster.redis: Still creating... [2m50s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m0s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m10s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m20s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m30s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m40s elapsed]
aws_elasticache_cluster.redis: Still creating... [3m50s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m0s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m10s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m20s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m30s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m40s elapsed]
aws_elasticache_cluster.redis: Still creating... [4m50s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m0s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m10s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m20s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m30s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m40s elapsed]
aws_elasticache_cluster.redis: Still creating... [5m50s elapsed]
aws_elasticache_cluster.redis: Still creating... [6m0s elapsed]
aws_elasticache_cluster.redis: Creation complete after 6m5s [id=bedrock-claude-redis]

Warning: Applied changes may be incomplete

The plan was created with the -target option in effect, so some changes
requested in the configuration may have been ignored and the output values
may not be fully updated. Run the following command to verify that no other
changes are pending:
    terraform plan
	
Note that the -target option is not suitable for routine use, and is provided
only for exceptional situations such as recovering from errors or mistakes,
or when Terraform specifically suggests to use it as part of an error
message.

Apply complete! Resources: 5 added, 0 changed, 0 destroyed.

Outputs:

auth_gateway_bedrock_role_arn = "arn:aws:iam::680877507363:role/bedrock-claude-auth-gateway-bedrock"
bedrock_ag_ecr_url = "680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/bedrock-access-gateway"
bedrock_ag_role_arn = tostring(null)
bedrock_role_arn = "arn:aws:iam::680877507363:role/bedrock-claude-bedrock-access"
cluster_autoscaler_role_arn = "arn:aws:iam::680877507363:role/bedrock-claude-cluster-autoscaler"
ecr_repository_url = "680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal"
efs_dns_name = "fs-0a2b5924041425002.efs.ap-northeast-2.amazonaws.com"
efs_file_system_id = "fs-0a2b5924041425002"
eks_cluster_endpoint = "https://77AD470D1F122F9E7322B2E662EA42DF.gr7.ap-northeast-2.eks.amazonaws.com"
eks_cluster_name = "bedrock-claude-eks"
eks_oidc_provider_arn = "arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF"
next_steps = <<EOT

=====================================================
Infrastructure provisioned! Next steps:
=====================================================

1. kubectl 설정:
   aws eks update-kubeconfig --name bedrock-claude-eks --region ap-northeast-2

2. Docker 이미지 push:
   aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal
   docker tag claude-code-terminal:latest 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest
   docker push 680877507363.dkr.ecr.ap-northeast-2.amazonaws.com/bedrock-claude/claude-code-terminal:latest

3. K8s manifests 배포:
   kubectl apply -f ../k8s/

=====================================================

EOT
redis_connection_url = "redis://bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com:6379/0"
redis_port = 6379
redis_primary_endpoint = "bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com"
redis_reader_endpoint = "bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com"
s3_vault_bucket_arn = "arn:aws:s3:::bedrock-claude-s3-vault-680877507363"
s3_vault_bucket_name = "bedrock-claude-s3-vault-680877507363"
s3_vault_kms_key_arn = "arn:aws:kms:ap-northeast-2:680877507363:key/bc47d786-64b9-42ae-8d03-58374253dd23"
vpc_id = "vpc-075deed66fcc7f348"

## Step 5 — Targeted Apply 결과 (2026-04-12)

Apply complete! Resources: 5 added, 0 changed, 0 destroyed.

| 리소스 | 결과 | 시간 |
|--------|------|------|
| aws_iam_openid_connect_provider.eks | ✅ Created | 1s |
| aws_security_group_rule.eks_to_efs | ✅ Created | 1s |
| aws_security_group_rule.eks_to_redis | ✅ Created | 1s |
| aws_iam_role_policy.auth_gateway_bedrock_invoke | ✅ Created | 1s |
| aws_elasticache_cluster.redis | ✅ Created | 6m5s |

## Step 6 — 검증 결과

### A. OIDC ARN 비교
- **신규 ARN**: `arn:aws:iam::680877507363:oidc-provider/oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF`
- **EKS OIDC issuer**: `https://oidc.eks.ap-northeast-2.amazonaws.com/id/77AD470D1F122F9E7322B2E662EA42DF`
- **결과**: EKS cluster가 생존하여 OIDC ID(`77AD470D1F122F9E7322B2E662EA42DF`) 동일 → ARN 불변 ✅

### B. Redis 상태
- **Status**: available ✅
- **New endpoint**: `bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com:6379`
- **REDIS_URL**: `redis://bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com:6379/0`
- ⚠️ **K8s secret `auth-gateway-secrets.REDIS_URL` 업데이트 필요** (클러스터 재생성으로 endpoint 변경)

### C. auth-gateway trust policy
- Federated: 신규 OIDC ARN과 일치 ✅
- SA: `system:serviceaccount:platform:platform-admin-sa` ✅

### D. claude-terminal-sa (bedrock-access role) trust policy
- Federated: 신규 OIDC ARN과 일치 ✅
- SA: `system:serviceaccount:claude-sessions:claude-terminal-sa` ✅

## 미완료 항목 (team-lead 지시 필요)
1. K8s secret `auth-gateway-secrets` REDIS_URL 업데이트: `redis://bedrock-claude-redis.khpawo.0001.apn2.cache.amazonaws.com:6379/0`
2. auth-gateway rollout restart (IRSA 재인증 + 새 REDIS_URL 적용)
3. kubectl apply (auth-gateway.yaml, onlyoffice.yaml) — Lane B K8s manifests
4. full terraform apply 잔여분 (system/ingress nodegroup, main 2→0 등)
