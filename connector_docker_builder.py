#!/usr/bin/env python3
import os
import sys
import argparse
import logging
from typing import List, Optional, Tuple
import yaml
import docker
import dotenv
import requests
from datetime import datetime


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        """
        Initialize Telegram notifier
        
        :param bot_token: Telegram bot token
        :param chat_id: Telegram chat ID to send messages to
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, message: str) -> bool:
        """
        Send message to Telegram
        
        :param message: Message to send
        :return: True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, json=data)
            return response.status_code == 200
        except Exception as e:
            logging.error(f"Failed to send Telegram notification: {e}")
            return False


class ConnectorDockerBuilder:
    def __init__(self, 
                 base_path: str, 
                 docker_hub_org: Optional[str] = None, 
                 tag: Optional[str] = None,
                 dry_run: bool = False,
                 skip_build: bool = False,
                 skip_push: bool = False,
                 ignore_list: Optional[List[str]] = None,
                 config_file: Optional[str] = None,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 platforms: Optional[List[str]] = None):
        """
        Initialize the Docker image builder for connectors
        """
        # Load .env file if it exists
        self._load_env_file()
        
        # Initialize Telegram notifier if credentials are available
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.telegram = None
        if telegram_token and telegram_chat_id:
            self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        
        # Initialize other parameters
        self.base_path = os.path.abspath(os.path.expanduser(base_path))
        if not os.path.isdir(self.base_path):
            raise ValueError(f"Invalid base path: {self.base_path}")
        
        self.config = self._load_config(config_file) if config_file else {}
        self.docker_hub_org = docker_hub_org or self.config.get('docker_hub_org') or os.getenv('DOCKER_HUB_ORG')
        self.tag = tag or self.config.get('tag', 'latest')
        self.dry_run = dry_run
        self.skip_build = skip_build
        self.skip_push = skip_push
        self.username = username or os.getenv('DOCKER_HUB_USERNAME')
        self.password = password or os.getenv('DOCKER_HUB_PASSWORD')
        
        default_platforms = ['linux/amd64', 'linux/arm64']
        self.platforms = platforms or self.config.get('platforms', default_platforms)
        
        default_ignore = ['.git', '.github', '__pycache__', 'venv', 'env']
        user_ignore = ignore_list or self.config.get('ignore_list', [])
        self.ignore_list = set(default_ignore + user_ignore)
        
        self._setup_logging()
        self.docker_client = docker.from_env()
        
        self.logger.info(f"Initialized Docker Image Builder")
        self._notify_start()

    def _load_env_file(self):
        """
        Load environment variables from a .env file if specified or found in the base directory.
        """
        env_file = os.getenv('ENV_FILE', '.env')  # Default to .env if ENV_FILE is not set
        if os.path.exists(env_file):
            dotenv.load_dotenv(env_file)
            print(f"Loaded environment variables from {env_file}")
        else:
            print("No .env file found or specified.")

    def _load_config(self, config_file: str):
        """
        Load configuration from a YAML file.
        """
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)

    def _setup_logging(self):
        """
        Set up logging for the builder.
        """
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger('ConnectorDockerBuilder')

    def _notify_start(self):
        """Send notification about build process start"""
        if self.telegram:
            message = (
                "🔄 <b>Docker Build Process Started</b>\n\n"
                f"🏢 Organization: {self.docker_hub_org or 'Not set'}\n"
                f"🏷️ Tag: {self.tag}\n"
                f"🖥️ Platforms: {', '.join(self.platforms)}\n"
                f"⏰ Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.telegram.send_message(message)

    def build_and_push_images(self) -> List[Tuple[str, bool]]:
        """Build and push Docker images for all connectors"""
        results = []
        
        for folder in os.listdir(self.base_path):
            connector_path = os.path.join(self.base_path, folder)
            
            if not os.path.isdir(connector_path) or folder in self.ignore_list:
                self.logger.info(f"Skipping {folder}: Invalid or ignored connector")
                continue
            
            image_name = f"{self.docker_hub_org}/{folder}:{self.tag}" if self.docker_hub_org else f"{folder}:{self.tag}"
            build_success = self._build_and_push_multiarch(connector_path, image_name)
            
            results.append((folder, build_success))
        
        self._notify_completion(results)
        return results

    def _build_and_push_multiarch(self, connector_path: str, image_name: str) -> bool:
        """
        Build and push a multi-architecture Docker image.
        """
        try:
            # Build the Docker image
            self.logger.info(f"Building image {image_name}...")
            if not self.dry_run:
                self.docker_client.images.build(path=connector_path, tag=image_name, platform=self.platforms)
            
            # Push the Docker image
            self.logger.info(f"Pushing image {image_name}...")
            if not self.dry_run and not self.skip_push:
                self.docker_client.images.push(image_name)
            
            self.logger.info(f"Successfully built and pushed {image_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to build or push {image_name}: {e}")
            return False

    def _notify_completion(self, results: List[Tuple[str, bool]]):
        """Send notification about overall build completion"""
        if self.telegram:
            total = len(results)
            successful = sum(1 for _, success in results if success)
            failed = total - successful
            
            message = (
                "🏁 <b>Build Process Completed</b>\n\n"
                f"📊 Summary:\n"
                f"- Total: {total}\n"
                f"- Successful: {successful}\n"
                f"- Failed: {failed}\n"
            )
            self.telegram.send_message(message)


def main():
    parser = argparse.ArgumentParser(description='Build and push multi-architecture Docker images for connectors')
    parser.add_argument('base_path', help='Base directory containing connector folders')
    parser.add_argument('-o', '--org', help='Docker Hub organization name')
    parser.add_argument('-t', '--tag', default='latest', help='Docker image tag')
    parser.add_argument('--dry-run', action='store_true', help='Simulate actions without executing')
    args = parser.parse_args()
    
    builder = ConnectorDockerBuilder(
        base_path=args.base_path,
        docker_hub_org=args.org,
        tag=args.tag,
        dry_run=args.dry_run
    )
    builder.build_and_push_images()


if __name__ == '__main__':
    main()
