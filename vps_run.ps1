# vps_run.ps1 — Launch a walk-forward run on an AWS spot instance
#
# Prerequisites:
#   1. AWS CLI installed and configured (aws configure)
#   2. An EC2 key pair created in the AWS console — set KEY_NAME below
#   3. Your Alpaca API key stored in the OS keychain (used by the backtest engine)
#
# Usage:
#   .\vps_run.ps1 -Start 2010-01-01 -Db wf_voltarget_2010.db
#   .\vps_run.ps1 -Start 2010-01-01 -Db wf_voltarget_2010.db -TrainMonths 12 -Trials 150 -Workers 12
#
# The script will:
#   1. Request a spot instance (c5.4xlarge, 16 vCPU, ~$0.25/hr)
#   2. Rsync data/daily/*.feather + stratscout source to the instance
#   3. Install Python deps remotely
#   4. Launch the walk-forward run with -Workers workers
#   5. Stream the log back to your terminal live
#   6. Rsync the .db result back when done
#   7. Terminate the instance automatically

param(
    [Parameter(Mandatory)][string]$Start,
    [Parameter(Mandatory)][string]$Db,
    [string]$End         = "",
    [int]   $TrainMonths = 12,
    [int]   $Trials      = 100,
    [int]   $Workers     = 12,
    [switch]$NoCalmar,
    [string]$Region      = "us-east-1",
    # Your EC2 key pair name (created in AWS Console → EC2 → Key Pairs)
    [string]$KeyName     = "stratscout",
    # Path to the .pem private key file downloaded when you created the key pair
    [string]$KeyFile     = "$env:USERPROFILE\.ssh\stratscout.pem"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$DataDir     = Join-Path $ProjectRoot "data\daily"

# ── 1. Find the latest Ubuntu 22.04 AMI ───────────────────────────────────────
Write-Host "Looking up latest Ubuntu 22.04 AMI..."
$AmiId = (aws ec2 describe-images `
    --region $Region `
    --owners 099720109477 `
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" `
              "Name=state,Values=available" `
    --query "reverse(sort_by(Images, &CreationDate))[0].ImageId" `
    --output text)
Write-Host "  AMI: $AmiId"

# ── 2. Get default VPC and subnet ─────────────────────────────────────────────
$VpcId = (aws ec2 describe-vpcs --region $Region `
    --filters "Name=isDefault,Values=true" `
    --query "Vpcs[0].VpcId" --output text)
$SubnetId = (aws ec2 describe-subnets --region $Region `
    --filters "Name=vpc-id,Values=$VpcId" `
    --query "Subnets[0].SubnetId" --output text)

# ── 3. Create a security group allowing SSH ────────────────────────────────────
$SgName = "stratscout-sg-$(Get-Date -Format 'yyyyMMddHHmm')"
Write-Host "Creating security group $SgName..."
$SgId = (aws ec2 create-security-group --region $Region `
    --group-name $SgName `
    --description "StratScout VPS temporary SG" `
    --vpc-id $VpcId `
    --query "GroupId" --output text)
aws ec2 authorize-security-group-ingress --region $Region `
    --group-id $SgId --protocol tcp --port 22 --cidr "0.0.0.0/0" | Out-Null

# ── 4. Request spot instance ───────────────────────────────────────────────────
Write-Host "Requesting c5.4xlarge spot instance..."
$LaunchSpec = @{
    ImageId          = $AmiId
    InstanceType     = "c5.4xlarge"
    KeyName          = $KeyName
    SecurityGroupIds = @($SgId)
    SubnetId         = $SubnetId
} | ConvertTo-Json -Compress

$SpotRequest = aws ec2 request-spot-instances --region $Region `
    --spot-price "0.50" `
    --instance-count 1 `
    --type "one-time" `
    --launch-specification $LaunchSpec | ConvertFrom-Json
$RequestId = $SpotRequest.SpotInstanceRequests[0].SpotInstanceRequestId

Write-Host "  Spot request ID: $RequestId — waiting for fulfillment..."
aws ec2 wait spot-instance-request-fulfilled --region $Region `
    --spot-instance-request-ids $RequestId
$InstanceId = (aws ec2 describe-spot-instance-requests --region $Region `
    --spot-instance-request-ids $RequestId `
    --query "SpotInstanceRequests[0].InstanceId" --output text)
Write-Host "  Instance: $InstanceId — waiting for it to be running..."
aws ec2 wait instance-running --region $Region --instance-ids $InstanceId
$PublicIp = (aws ec2 describe-instances --region $Region `
    --instance-ids $InstanceId `
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
Write-Host "  Public IP: $PublicIp"

# Give SSH a moment to come up
Write-Host "Waiting 30s for SSH to become available..."
Start-Sleep -Seconds 30

$Ssh    = "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no ubuntu@$PublicIp"
$Remote = "ubuntu@${PublicIp}"

# ── 5. Install deps on remote ─────────────────────────────────────────────────
Write-Host "Installing Python deps on remote..."
& ssh -i "$KeyFile" -o StrictHostKeyChecking=no ubuntu@$PublicIp @"
sudo apt-get update -qq && sudo apt-get install -y python3-pip python3-venv rsync -qq
python3 -m venv ~/venv
~/venv/bin/pip install -q optuna pandas pyarrow fastparquet scikit-learn scipy ephem pylunar 2>&1 | tail -5
"@

# ── 6. Rsync source + data ────────────────────────────────────────────────────
Write-Host "Syncing stratscout source (~1 min first time)..."
& rsync -az --exclude="__pycache__" --exclude="*.pyc" --exclude="web/node_modules" `
    -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
    "$PSScriptRoot/" "${Remote}:~/stratscout/"

Write-Host "Syncing feather data files (~3-5 min first time)..."
& rsync -az --progress `
    -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
    "$DataDir/" "${Remote}:~/data/daily/"

# Sync HoF db if it exists
$HofPath = Join-Path $ProjectRoot "stratscout\engine\data\params_hof.db"
if (Test-Path $HofPath) {
    & rsync -az -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
        "$HofPath" "${Remote}:~/stratscout/engine/data/params_hof.db"
}

# ── 7. Build the remote command ───────────────────────────────────────────────
$RemoteCmd = "cd ~ && PYTHONPATH=. PYTHONIOENCODING=utf-8 ~/venv/bin/python -m stratscout.engine.fuzzers.walk_forward_etf"
$RemoteCmd += " --start $Start"
if ($End)          { $RemoteCmd += " --end $End" }
$RemoteCmd += " --trials $Trials --optuna --train-months $TrainMonths --workers $Workers"
$RemoteCmd += " --db $Db"
if ($NoCalmar)     { $RemoteCmd += " --no-calmar" }
$RemoteCmd += " 2>&1 | tee ~/$Db.log"

# ── 8. Run and stream log ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "=========================================="
Write-Host " Launching walk-forward on $PublicIp"
Write-Host " $Workers workers, $Trials trials/month"
Write-Host " DB: $Db"
Write-Host "=========================================="
Write-Host ""
& ssh -i "$KeyFile" -o StrictHostKeyChecking=no ubuntu@$PublicIp $RemoteCmd

# ── 9. Rsync results back ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "Syncing results back..."
& rsync -az -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
    "${Remote}:~/$Db" "$ProjectRoot\$Db"
& rsync -az -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
    "${Remote}:~/$Db.log" "$ProjectRoot\$($Db -replace '\.db$','').log"
# Sync updated HoF back
& rsync -az -e "ssh -i `"$KeyFile`" -o StrictHostKeyChecking=no" `
    "${Remote}:~/stratscout/engine/data/params_hof.db" "$HofPath"

Write-Host ""
Write-Host "Done. Results saved to $ProjectRoot\$Db"

# ── 10. Terminate instance ────────────────────────────────────────────────────
Write-Host "Terminating instance $InstanceId..."
aws ec2 terminate-instances --region $Region --instance-ids $InstanceId | Out-Null
aws ec2 delete-security-group --region $Region --group-id $SgId | Out-Null
Write-Host "Instance terminated. All done."
