resource "aws_iam_role" "glue" {
  name                 = "${local.name_prefix}_glue_${var.env}"
  permissions_boundary = var.permissions_boundary

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue" {
  name = "${local.name_prefix}_${var.env}"
  role = aws_iam_role.glue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "ArtifactsRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.artifacts_arn}/${local.s3_prefix}/*"]
      },
      {
        Sid       = "ArtifactsList"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [local.artifacts_arn]
        Condition = { StringLike = { "s3:prefix" = ["${local.s3_prefix}/*"] } }
      },
      {
        Sid      = "ArtifactsTempWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.artifacts_arn}/${local.tmp_prefix}/*"]
      },
      {
        Sid      = "DataBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject"]
        Resource = flatten([for b in var.data_bucket_names : ["arn:aws:s3:::${b}", "arn:aws:s3:::${b}/*"]])
      },
      {
        Sid      = "DbSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [local.secret_arn]
      },
      {
        Sid       = "Email"
        Effect    = "Allow"
        Action    = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource  = ["*"]
        Condition = { StringEquals = { "ses:FromAddress" = var.notify_from_email } }
      },
      ], length(var.kms_key_arns) == 0 ? [] : [{
        Sid      = "DataBucketKeys"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = var.kms_key_arns
    }])
  })
}
