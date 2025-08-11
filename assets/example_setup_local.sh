#!/bin/bash
scp -o StrictHostKeyChecking=no ~/.ssh/github_key {host}:/root/.ssh/github_key
