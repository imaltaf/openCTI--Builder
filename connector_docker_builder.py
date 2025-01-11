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
import time
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
        def _load_env_file(self):
    """
    Load environment variables from a .env file if specified or found in the base directory.
    """
    env_file = os.getenv('ENV_FILE', '.env')
    if os.path.exists(env_file):
        dotenv.load_dotenv(env_file)
        self.logger.info(f"Loaded environment variables from {env_file}")
    else:
        self.logger.info("No .env file found or specified.")

        # Load .env file if it exists
        self._load_env_file()
        
        # Initialize Telegram notifier if credentials are available
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.telegram = None
        if telegram_token and telegram_chat_id:
            self.telegram = TelegramNotifier(telegram_token, telegram_chat_id)
        
        # Rest of the initialization code remains the same
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

    def _notify_build_status(self, connector: str, image_name: str, success: bool):
        """Send notification about individual build status"""
        if self.telegram:
            status_emoji = "✅" if success else "❌"
            status_text = "succeeded" if success else "failed"
            message = (
                f"{status_emoji} <b>Build {status_text}</b>\n\n"
                f"🔧 Connector: {connector}\n"
                f"🏷️ Image: {image_name}\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.telegram.send_message(message)

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
                f"- Failed: {failed}\n\n"
                f"⏰ Completion Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            if failed > 0:
                message += "\n\n❌ Failed builds:"
                for connector, success in results:
                    if not success:
                        message += f"\n- {connector}"
            
            self.telegram.send_message(message)

    def build_and_push_images(self) -> List[Tuple[str, bool]]:
        """Build and push Docker images for all connectors"""
        results = []
        
        for folder in os.listdir(self.base_path):
            connector_path = os.path.join(self.base_path, folder)
            
            if not self._validate_connector(connector_path):
                self.logger.info(f"Skipping {folder}: Invalid connector")
                continue
            
            prefixed_folder = f"connector-{folder}"
            image_tags = [self.tag]
            if self.tag != 'latest':
                image_tags.append('latest')
            
            success = True
            for tag in image_tags:
                image_name = (f"{self.docker_hub_org}/{prefixed_folder}:{tag}" 
                            if self.docker_hub_org 
                            else f"{prefixed_folder}:{tag}")
                
                build_success = self._build_and_push_multiarch(connector_path, image_name)
                self._notify_build_status(folder, image_name, build_success)
                success = success and build_success
            
            results.append((folder, success))
        
        self._notify_completion(results)
        return results

    # Rest of the methods (_load_env_file, _load_config, _setup_logging, 
    # _validate_connector, _build_and_push_multiarch) remain the same

def main():
    parser = argparse.ArgumentParser(description='Build and push multi-architecture Docker images for connectors')
    parser.add_argument('base_path', help='Base directory containing connector folders')
    parser.add_argument('-o', '--org', help='Docker Hub organization name')
    parser.add_argument('-t', '--tag', default='latest', help='Docker image tag')
    parser.add_argument('--dry-run', action='store_true', help='Simulate actions without executing')
    parser.add_argument('--skip-build', action='store_true', help='Skip building Docker images')
    parser.add_argument('--skip-push', action='store_true', help='Skip pushing Docker images')
    parser.add_argument('--ignore', nargs='+', help='List of folders to ignore')
    parser.add_argument('-c', '--config', help='Path to configuration YAML file')
    parser.add_argument('-u', '--username', help='Docker Hub username')
    parser.add_argument('-p', '--password', help='Docker Hub password or token')
    parser.add_argument('--platforms', nargs='+', 
                        help='Platforms to build for (e.g., linux/amd64 linux/arm64)')
    parser.add_argument('--env', help='Path to .env file')
    
    args = parser.parse_args()
    
    try:
        builder = ConnectorDockerBuilder(
            base_path=args.base_path,
            docker_hub_org=args.org,
            tag=args.tag,
            dry_run=args.dry_run,
            skip_build=args.skip_build,
            skip_push=args.skip_push,
            ignore_list=args.ignore,
            config_file=args.config,
            username=args.username,
            password=args.password,
            platforms=args.platforms
        )
        
        results = builder.build_and_push_images()
        
        total_connectors = len(results)
        successful_connectors = sum(1 for _, success in results if success)
        
        print("\n--- Build and Push Summary ---")
        print(f"Total Connectors: {total_connectors}")
        print(f"Successful Builds: {successful_connectors}")
        print(f"Failed Builds: {total_connectors - successful_connectors}")
        
        print("\nDetailed Results:")
        for connector, success in results:
            status = "✅ Success" if success else "❌ Failed"
            print(f"{connector}: {status}")
        
        sys.exit(0 if successful_connectors == total_connectors else 1)
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()