resource "aws_iam_role" "glue" {
  name = "${var.name_prefix}_glue_${var.environment}"

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
  name = "${var.name_prefix}_${var.environment}"
  role = aws_iam_role.glue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "ArtifactsRead"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      },
      {
        Sid      = "ArtifactsTempWrite"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.artifacts.arn}/${var.artifacts_bucket_key}/temp/*"]
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
        Resource = [aws_secretsmanager_secret.db.arn]
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

resource "aws_glue_connection" "vpc" {
  count           = var.enable_vpc_connection ? 1 : 0
  name            = "${var.name_prefix}_vpc_${var.environment}"
  connection_type = "NETWORK"

  physical_connection_requirements {
    subnet_id              = var.subnet_id
    security_group_id_list = var.security_group_ids
    availability_zone      = var.availability_zone
  }

  tags = local.tags
}
