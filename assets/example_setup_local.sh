#!/bin/bash
scp -o StrictHostKeyChecking=no ~/.ssh/github_key {alias}:/root/.ssh/github_key
