#!/bin/bash
scp -o StrictHostKeyChecking=no ~/.ssh/github_key $POD_HOST:/root/.ssh/github_key
