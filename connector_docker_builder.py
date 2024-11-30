#!/usr/bin/env python3
import os
import sys
import argparse
import logging
from typing import List, Optional, Tuple
import yaml
import docker
import dotenv  # New dependency for .env file support

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
        
        :param base_path: Base directory containing connector folders
        :param docker_hub_org: Docker Hub organization name
        :param tag: Custom tag for Docker images
        :param dry_run: If True, only print commands without executing
        :param skip_build: Skip building Docker images
        :param skip_push: Skip pushing Docker images
        :param ignore_list: List of folders to ignore
        :param config_file: Path to a configuration YAML file
        :param username: Docker Hub username
        :param password: Docker Hub password or token
        :param platforms: List of platforms to build for
        """
        # Load .env file if it exists
        self._load_env_file()
        
        # Ensure the base path is an absolute, expanded path
        self.base_path = os.path.abspath(os.path.expanduser(base_path))
        
        # Validate base path exists
        if not os.path.isdir(self.base_path):
            raise ValueError(f"Invalid base path: {self.base_path}")
        
        # Load configuration from file if provided
        self.config = self._load_config(config_file) if config_file else {}
        
        # Override config with direct parameters
        self.docker_hub_org = docker_hub_org or self.config.get('docker_hub_org') or os.getenv('DOCKER_HUB_ORG')
        self.tag = tag or self.config.get('tag', 'latest')
        self.dry_run = dry_run
        self.skip_build = skip_build
        self.skip_push = skip_push
        
        # Docker credentials
        self.username = username or os.getenv('DOCKER_HUB_USERNAME')
        self.password = password or os.getenv('DOCKER_HUB_PASSWORD')
        
        # Build platforms
        default_platforms = ['linux/amd64', 'linux/arm64']
        self.platforms = platforms or self.config.get('platforms', default_platforms)
        
        # Prepare ignore list
        default_ignore = ['.git', '.github', '__pycache__', 'venv', 'env']
        user_ignore = ignore_list or self.config.get('ignore_list', [])
        self.ignore_list = set(default_ignore + user_ignore)
        
        # Setup logging
        self._setup_logging()
        
        # Docker client
        self.docker_client = docker.from_env()
        
        # Log initialization details
        self.logger.info(f"Initialized Docker Image Builder")
        self.logger.info(f"Base Path: {self.base_path}")
        self.logger.info(f"Docker Hub Org: {self.docker_hub_org or 'Not set'}")
        self.logger.info(f"Image Tag: {self.tag}")
        self.logger.info(f"Platforms: {', '.join(self.platforms)}")

    def _load_env_file(self, env_path: Optional[str] = None):
        """
        Load environment variables from .env file
        
        :param env_path: Optional path to .env file
        """
        # Try to load .env file from specified path or default locations
        env_paths = [
            env_path,
            os.path.join(os.getcwd(), '.env'),
            os.path.expanduser('~/.env')
        ]
        
        for path in env_paths:
            if path and os.path.exists(path):
                dotenv.load_dotenv(path)
                return

    def _load_config(self, config_path: str) -> dict:
        """
        Load configuration from a YAML file
        
        :param config_path: Path to the configuration YAML file
        :return: Parsed configuration dictionary
        """
        try:
            # Expand and absolute-ize the config path
            config_path = os.path.abspath(os.path.expanduser(config_path))
            
            with open(config_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            self.logger.warning(f"Config file {config_path} not found.")
            return {}
        except yaml.YAMLError as e:
            self.logger.error(f"Error parsing config file: {e}")
            sys.exit(1)

    def _setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        self.logger = logging.getLogger(__name__)

    def _validate_connector(self, connector_path: str) -> bool:
        """
        Validate if a connector directory is valid for processing
        
        :param connector_path: Path to the connector directory
        :return: True if connector is valid, False otherwise
        """
        # Check if it's a directory
        if not os.path.isdir(connector_path):
            return False
        
        # Check if directory name is in ignore list
        connector_name = os.path.basename(connector_path)
        if connector_name in self.ignore_list:
            return False
        
        # Check for Dockerfile
        dockerfile_path = os.path.join(connector_path, 'Dockerfile')
        return os.path.exists(dockerfile_path)

    def build_and_push_images(self) -> List[Tuple[str, bool]]:
        """
        Build and push Docker images for all connectors
        
        :return: List of tuples (connector_name, success_status)
        """
        results = []
        
        # Find and process all valid connector directories
        for folder in os.listdir(self.base_path):
            connector_path = os.path.join(self.base_path, folder)
            
            # Validate the connector
            if not self._validate_connector(connector_path):
                self.logger.info(f"Skipping {folder}: Invalid connector")
                continue
            
            # Prepare image name
            image_name = (f"{self.docker_hub_org}/{folder}:{self.tag}" 
                          if self.docker_hub_org 
                          else f"{folder}:{self.tag}")
            
            # Build and push multi-arch image
            success = self._build_and_push_multiarch(connector_path, image_name)
            
            results.append((folder, success))
        
        return results

    def _build_and_push_multiarch(self, context_path: str, image_name: str) -> bool:
        """
        Build and push multi-architecture Docker image
        
        :param context_path: Path to the build context
        :param image_name: Name of the Docker image
        :return: True if build and push successful, False otherwise
        """
        if self.skip_build:
            self.logger.info(f"Skipping build for {image_name}")
            return True
        
        try:
            if self.dry_run:
                self.logger.info(f"[DRY RUN] Would build multi-arch image: {image_name}")
                return True
            
            self.logger.info(f"Building multi-arch image: {image_name}")
            
            # Prepare buildx command
            platforms_str = ','.join(self.platforms)
            
            # Prepare docker build command with buildx
            build_args = [
                'docker', 'buildx', 'build',
                '--platform', platforms_str,
                '-t', image_name,
                context_path,
                '--push' if not self.skip_push and self.docker_hub_org else ''
            ]
            
            # Remove empty string if skip_push is True or no docker_hub_org
            build_args = [arg for arg in build_args if arg]
            
            # Run buildx command
            import subprocess
            result = subprocess.run(build_args, capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info(f"Successfully built and pushed {image_name}")
                return True
            else:
                self.logger.error(f"Build failed for {image_name}")
                self.logger.error(result.stderr)
                return False
        
        except Exception as e:
            self.logger.error(f"Unexpected error building {image_name}: {e}")
            return False

def main():
    """
    Main function to parse arguments and run the connector Docker image builder
    """
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
        # Create builder instance
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
        
        # Build and push images
        results = builder.build_and_push_images()
        
        # Print summary
        total_connectors = len(results)
        successful_connectors = sum(1 for _, success in results if success)
        
        print("\n--- Build and Push Summary ---")
        print(f"Total Connectors: {total_connectors}")
        print(f"Successful Builds: {successful_connectors}")
        print(f"Failed Builds: {total_connectors - successful_connectors}")
        
        # Detailed report of successful and failed builds
        print("\nDetailed Results:")
        for connector, success in results:
            status = "✅ Success" if success else "❌ Failed"
            print(f"{connector}: {status}")
        
        # Exit with non-zero status if any builds failed
        sys.exit(0 if successful_connectors == total_connectors else 1)
    
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)



if __name__ == '__main__':
    main()