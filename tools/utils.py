# lint as: python3
###############################################################################
#
# MIT License
#
# Copyright (c) 2024 Advanced Micro Devices, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
#################################################################################
"""A set of utility functions for the application

This module provides a set of utility functions that are used by the application.

Classes:
    Console: A class to run shell commands in the console.
    Docker: A class to run shell commands in a Docker container.
    Timeout: A class to handle timeouts.
    RunDetails: A class to store the run details.

Functions:
    get_gpu_vendor: Get the GPU vendor.
    get_dockerfile_gpu_suffix: Get the GPU Dockerfile suffix based on the GPU vendor.
    get_host_os: Get the host operating system.
    get_dockerfile_linux_suffix: Get the Linux Dockerfile suffix based on the host OS.
    get_system_cpus: Get the number of CPUs in the system.
    get_system_gpus: Get the number of GPUs in the system.
    get_gpu_renderD_nodes: Get the GPU renderD nodes in the system.
    get_docker_gpus: Get the number of GPUs in Docker.
    get_system_gpu_arch: Get the GPU architecture in the system.
    get_gpu_docker_args: Get the GPU Docker arguments based on the GPU vendor.
    get_cpu_docker_args: Get the CPU Docker arguments.
    get_env_docker_args: Get the environment Docker arguments.
    get_mount_docker_args: Get the mount Docker arguments.
    get_base_docker: Get the base Docker image.
    get_base_docker_sha: Get the base Docker image SHA.
    get_host_name: Get the host name.
    load_models: Load the models from the models.json file.
    read_log_file: Read the log file.
    get_perf_metric: Parse the performance metric.
"""

import urllib
import os
import sys
import json
import csv
import typing
import subprocess
import signal
import re
import collections.abc
import pandas as pd

from logger import get_logger

logger = get_logger("MAD")
logger.removeHandler(logger.handlers[0])


# ==================================================================================================
# Classes
# ==================================================================================================
class Console:
    """A class to run shell commands in the console.

    Attributes:
        shell_verbose (bool): Whether to print the shell command.
        live_output (bool): Whether to print the shell output in real-time.

    Methods:
        sh: Run a shell command.
    """

    def __init__(self, shell_verbose: bool = True, live_output: bool = False) -> None:
        """Initialize the Console class.

        Args:
            shell_verbose (bool): Whether to print the shell command.
            live_output (bool): Whether to print the shell output in real-time.

        Returns:
            None
        """
        self.shell_verbose = shell_verbose
        self.live_output = live_output

    def sh(
        self,
        command: str,
        can_fail: bool = False,
        timeout: int = 60,
        secret: bool = False,
        prefix: str = "",
        env: typing.Optional[typing.Dict] = None,
    ) -> str:
        """Run a shell command.

        Args:
            command (str): The shell command.
            can_fail (bool): Whether the command can fail.
            timeout (int): The command timeout.
            secret (bool): Whether the command is secret.
            prefix (str): The prefix for the output.
            env (dict): The environment variables.

        Returns:
            str: The shell output.

        Raises:
            RuntimeError: If the command fails.
        """
        # Print the shell command
        if self.shell_verbose and not secret:
            logger.info(f"> {command}")
            # print("> " + command, flush=True)

        # Run the shell command
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            universal_newlines=True,
            bufsize=1,
            env=env,
        )

        # Get the shell output
        try:
            if not self.live_output:
                # If live output is disabled, read the output at the end, not in real-time.
                outs, errs = proc.communicate(timeout=timeout)
                logger.info(f"{prefix}{outs}")
                if errs:
                    logger.error(f"{prefix}{errs}")
            else:
                # If live output is enabled, read the output in real-time.
                outs = []
                # Read the output line by line
                for stdout_line in iter(proc.stdout.readline, ""):
                    logger.info(f"{prefix}{stdout_line}")
                    # print(prefix + stdout_line, end="")
                    outs.append(stdout_line)

                # Join the output lines
                outs = "".join(outs)
                proc.stdout.close()
                proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # Kill the process if it times out
            logger.error("Console script timeout")
            proc.kill()
            raise RuntimeError("Console script timeout") from exc
        except Exception as exc:
            # Handle other exceptions
            logger.error("Console script failed")
            raise RuntimeError("Console script failed") from exc

        # Check the return code,
        # if the command fails, raise an exception,
        # if it can fail, return the output.
        if proc.returncode != 0:
            if not can_fail:
                if not secret:
                    logger.error(
                        f"Subprocess '{command}' failed with exit code {proc.returncode}"
                    )
                    raise RuntimeError(
                        "Subprocess '"
                        + command
                        + "' failed with exit code "
                        + str(proc.returncode)
                    )
                else:
                    logger.error(
                        f"Subprocess '{secret}' failed with exit code {proc.returncode}"
                    )
                    raise RuntimeError(
                        "Subprocess '"
                        + secret
                        + "' failed with exit code "
                        + str(proc.returncode)
                    )
        return outs.strip()


class Docker:
    """A class to run shell commands in a Docker container.

    Attributes:
        docker_sha (str): The Docker SHA.
        keep_alive (bool): Whether to keep the container alive.
        console (Console): The console.
        userid (str): The user ID.
        groupid (str): The group ID.

    Methods:
        sh: Run a shell command in the Docker container.

    Note:
        The command is run as root.
    """

    def __init__(
        self,
        image: str,
        container_name: str,
        docker_opts: str,
        mounts: typing.Optional[typing.List] = None,
        env_vars: typing.Optional[typing.Dict] = None,
        keep_alive: bool = False,
        console: Console = Console(),
    ) -> None:
        """Initialize the Docker class.

        Args:
            image (str): The Docker image.
            container_name (str): The container name.
            docker_opts (str): The Docker options.
            mounts (list): The mounts.
            env_vars (dict): The environment variables.
            keep_alive (bool): Whether to keep the container alive.
            console (Console): The console.

        Returns:
            None

        Raises:
            RuntimeError: If the container name already exists.
        """
        # Initialize the variables of the Docker class.
        self.docker_sha = None
        self.keep_alive = keep_alive
        cwd = os.getcwd()
        self.console = console
        self.userid = self.console.sh("id -u")
        self.groupid = self.console.sh("id -g")

        # Check if container name exists, if yes, raise error, else proceed.
        container_name_exists = self.console.sh(
            "docker container ps -a | grep " + container_name + " | wc -l"
        )
        if container_name_exists != "0":
            logger.error(
                "Container with name, "
                + container_name
                + " already exists. "
                + "Please stop (docker stop --time=1 SHA) and remove this (docker rm -f SHA) to proceed.."
            )
            raise RuntimeError(
                "Container with name, "
                + container_name
                + " already exists. "
                + "Please stop (docker stop --time=1 SHA) and remove this (docker rm -f SHA) to proceed.."
            )

        # Build the docker run command
        command = (
            "docker run -t -d -u "
            + self.userid
            + ":"
            + self.groupid
            + " "
            + docker_opts
            + " "
        )
        # Add mounts, if any.
        if mounts is not None:
            for mount in mounts:
                command += "-v " + mount + ":" + mount + " "

        # Add current working directory as mount.
        command += "-v " + cwd + ":/myworkspace/ "

        # Add environment variables, if any.
        if env_vars is not None:
            # Iterate over the environment variables and add them to the command.
            for evar in env_vars.keys():
                command += "-e " + evar + "=" + env_vars[evar] + " "

        # Add the image and container name to the command.
        command += "--workdir /myworkspace/ "
        command += "--name " + container_name + " "
        command += image + " "

        # Hack to keep the container alive.
        command += "cat "

        # Run the docker run command.
        self.console.sh(command)

        # Get the SHA of the container.
        self.docker_sha = self.console.sh(
            "docker ps -aqf 'name=" + container_name + "' "
        )

    def sh(self, command: str, timeout: int = 60, secret: bool = False) -> str:
        """Run a shell command in the Docker container.

        Args:
            command (str): The shell command.
            timeout (int): The command timeout.
            secret (bool): Whether the command is secret.

        Returns:
            str: The shell output.

        Raises:
            RuntimeError: If the command fails.

        Note:
            The command is run as root.
        """
        return self.console.sh(
            "docker exec " + self.docker_sha + ' bash -c "' + command + '"',
            timeout=timeout,
            secret=secret,
        )

    def __del__(self):
        """Delete the Docker container.

        Returns:
            None

        Note:
            If the keep_alive flag is set, the Docker container is kept alive.
        """
        if not self.keep_alive and self.docker_sha:
            # If keep_alive is False, stop and remove the Docker container.
            logger.info("Stopping and removing the Docker container")
            self.console.sh("docker stop --time=1 " + self.docker_sha)
            self.console.sh("docker rm -f " + self.docker_sha)
            return

        if self.docker_sha:
            # If keep_alive is True, print the Docker commands to keep the container alive.
            logger.info("==========================================")
            logger.info(f"Keeping docker alive, sha : {self.docker_sha}")
            logger.info(
                f"Open a bash session in container : docker exec -it {self.docker_sha} bash"
            )
            logger.info(f"Stop container : docker stop --time=1 {self.docker_sha}")
            logger.info(f"Remove container : docker rm -f {self.docker_sha}")
            logger.info("==========================================")


class Timeout:
    """A class to handle timeouts.

    Attributes:
        seconds (int): The timeout in seconds.

    Methods:
        handle_timeout: Handle the timeout.
    """

    def __init__(self, seconds: int = 15) -> None:
        """Initialize the Timeout class.

        Args:
            seconds (int): The timeout in seconds.

        Returns:
            None
        """
        self.seconds = seconds

    def handle_timeout(self, signum, frame):
        """Handle the timeout.

        Args:
            signum: The signal number.
            frame: The frame.

        Returns:
            None

        Raises:
            TimeoutError: If the program times out.
        """
        logger.error("TimeoutError: Program timed out")
        raise TimeoutError("Program timed out. Requested timeout=" + str(self.seconds))

    def __enter__(self) -> None:
        """Enter the timeout context."""
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self.seconds)

    def __exit__(self, type, value, traceback) -> None:
        """Exit the timeout context."""
        signal.alarm(0)


class RunDetails:
    """A class to store the run details.

    Attributes:
        model (str): The model name.
        pipeline (str): The model pipeline.
        args (str): The model arguments.
        tags (str): The model tags.
        docker_file (str): The model Dockerfile.
        base_docker (str): The model base Docker image.
        docker_sha (str): The model Docker image SHA.
        docker_image (str): The model Docker image.
        machine_name (str): The machine name.
        gpu_architecture (str): The system GPU architecture.
        n_gpus (str): The system number of GPUs.
        training_precision (str): The training precision.
        performance (str): The performance.
        metric (str): The metric.
        status (str): The status.
        build_duration (str): The build duration.
        test_duration (str): The test duration.

    Methods:
        __init__: Initialize the RunDetails class.
        print_summary: Print the performance metrics.
        print_perf_metric: Print the performance metrics.
        generate_report: Generate the performance report.
    """

    def __init__(self) -> None:
        """Initialize the RunDetails class."""
        self.model = ""
        self.pipeline = ""
        self.tags = ""
        self.args = ""
        self.docker_file = ""
        self.base_docker = ""
        self.docker_sha = ""
        self.docker_image = ""
        self.machine_name = ""
        self.host_os = ""
        self.gpu_architecture = ""
        self.n_gpus = ""
        self.training_precision = ""
        self.performance = ""
        self.metric = ""
        self.status = "FAILURE"
        self.build_duration = ""
        self.test_duration = ""
        self.git_commit = ""
        self.relative_change = ""

    def print_summary(self) -> None:
        """Print the performance metrics."""
        logger.info(
            f"Model: {self.model}, "
            f"Machine: {self.machine_name}, "
            f"GPU: {self.n_gpus}, "
            f"GPU Arch: {self.gpu_architecture}, "
            f"Precision: {self.training_precision}, "
            f"Performance: {self.performance}, "
            f"Metric: {self.metric}, "
            f"Status: {self.status}, "
            f"Build Duration: {self.build_duration}, "
            f"Test Duration: {self.test_duration}"
        )

    def print_perf_metric(self) -> None:
        """Print the performance metric."""
        logger.info(
            f"{self.model} performance is {self.performance} {self.metric}"
        )

    def generate_json(self, json_name, multiple_results=False):
        """Generate the performance json.

        Args:
            json_name (str): The json name which is a json file.
            multiple_results (bool): Whether there are multiple results.

        Returns:
            None
        """
        keys_to_exclude = (
            {"model", "performance", "metric", "status"}
            if multiple_results
            else {}
        )
        attributes = vars(self)
        output_dict = {x: attributes[x] for x in attributes if x not in keys_to_exclude}
        with open(json_name, "w") as f:
            json.dump(output_dict, f, indent=4)

    def generate_report(
        self, report_name: str, are_multiple_results: bool = False
    ) -> None:
        """Generate the performance report.

        Args:
            report_name (str): The report name which is a json file.
            are_multiple_results (bool): Whether there are multiple results.

        Returns:
            None
        """
        keys_to_exclude = (
            {"model", "performance", "metric", "status"}
            if are_multiple_results
            else {}
        )
        attributes = vars(self)
        output_dict = {x: attributes[x] for x in attributes if x not in keys_to_exclude}

        # Write the output_dict to the csv file output.
        if os.path.isfile(report_name) and os.path.getsize(report_name) > 0:
            # Append the output_dict to the existing file.
            with open(report_name, "a") as f:
                w = csv.DictWriter(f, output_dict.keys())
                w.writerow(output_dict)
        else:
            # Write the output_dict to a new file.
            with open(report_name, "w") as f:
                w = csv.DictWriter(f, output_dict.keys())
                w.writeheader()
                w.writerow(output_dict)


# ==================================================================================================
# Utility functions
# ==================================================================================================

from typing import List

def subprocess_run(cmd: List[str]):
    import subprocess

    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def get_gpu_vendor() -> str:
    """Get the GPU vendor.

    Returns:
        str: The GPU vendor.

    Raises:
        Exception: If the GPU vendor is not NVIDIA or AMD.
    """
    # checks both command not installed, and installed but not working
    ERRORS = (FileNotFoundError, subprocess.CalledProcessError)

    try:
        _ = subprocess_run(["/usr/bin/nvidia-smi"])

    except ERRORS as e1:
        try:
            _ = subprocess_run(["/opt/rocm/bin/rocm-smi"])

        except ERRORS as e2:
            raise Exception("Unsupported GPU: Neither AMD nor NVIDIA")
        else:
            gpu_vendor = "AMD"
    else:
        gpu_vendor = "NVIDIA"

    logger.debug(f"GPU vendor: {gpu_vendor}")
    return gpu_vendor


def get_dockerfile_gpu_suffix() -> str:
    """Get the GPU Dockerfile suffix based on the GPU vendor.

    Returns:
        str: The GPU Dockerfile suffix.
    """
    try:
        gpu_vendor = get_gpu_vendor()
    except Exception as e:
        logger.error(f"Failed to get GPU vendor: {e}")
        sys.exit(1)

    if gpu_vendor == "NVIDIA":
        return ".nvidia"
    elif gpu_vendor == "AMD":
        return ".amd"
    else:
        return ""


def get_host_os() -> str:
    """Get the host operating system.

    Returns:
        str: The host operating system.
    """
    if os.path.exists("/usr/bin/apt"):
        host_os = "HOST_UBUNTU"
    elif os.path.exists("/usr/bin/yum"):
        host_os = "HOST_CENTOS"
    elif os.path.exists("/usr/bin/zypper"):
        host_os = "HOST_SLES"
    else:
        raise Exception(f"Unsupported host OS: {host_os}")

    logger.info(f"Host OS: {host_os}")
    return host_os


def get_dockerfile_linux_suffix() -> str:
    """Get the Linux Dockerfile suffix based on the host OS.

    Returns:
        str: The Linux Dockerfile suffix.
    """
    try:
        linux_dist = get_host_os()
    except Exception as e:
        logger.error(f"Failed to get host OS: {e}")
        sys.exit(1)

    if linux_dist == "HOST_UBUNTU":
        return ".ubuntu"
    elif linux_dist == "HOST_CENTOS":
        return ".centos"
    elif linux_dist == "HOST_SLES":
        return ".sles"
    else:
        return ""


def get_system_cpus() -> int:
    """Get the number of CPUs in the system.

    Returns:
        int: The number of CPUs in the system.
    """
    number_cpus = os.cpu_count()
    logger.info(f"Number of CPUs: {number_cpus}")
    return number_cpus


def get_system_gpus() -> int:
    """Get the number of GPUs in the system.

    Returns:
        int: The number of GPUs in the system.
    """
    number_gpus = 0
    try:
        gpu_vendor = get_gpu_vendor()
    except Exception as e:
        logger.error(f"Failed to get GPU vendor: {e}")
        sys.exit(1)

    if gpu_vendor == "NVIDIA":
        number_gpus = int(
            subprocess.check_output(
                "nvidia-smi --query-gpu=count --format=csv,noheader | head -n 1", shell=True
            )
        )
    elif gpu_vendor == "AMD":
        number_gpus = int(
            subprocess.check_output(
                "rocm-smi --showid --csv | grep card | wc -l", shell=True
            )
        )
    else:
        raise Exception(f"Unsupported GPU vendor: {gpu_vendor}")

    logger.info(f"Number of GPUs: {number_gpus}")
    return number_gpus


def get_gpu_renderD_nodes():
    """Get the GPU renderD nodes in the system.

    Returns:
        list: The GPU renderD nodes in the system.
    """
    gpu_renderDs = None
    try:
        gpu_vendor = get_gpu_vendor()
    except Exception as e:
        logger.error(f"Failed to get GPU vendor: {e}")
        sys.exit(1)

    if gpu_vendor == "AMD":
        renderDs = (
            subprocess.check_output(
                "grep -r drm_render_minor /sys/devices/virtual/kfd/kfd/topology/nodes",
                shell=True,
            )
            .decode("utf-8")
            .split("\n")
        )
        # Get the renderD nodes, just looking at the numberic value at the end.
        renderDs = [int(item.split()[-1]) for item in renderDs[:-1]]
        # Remove the 0th renderD node which is CPUs.
        gpu_renderDs = [x for x in renderDs if x != 0]
        gpu_renderDs.sort()

    return gpu_renderDs


def get_docker_gpus() -> typing.Optional[int]:
    """Get the number of GPUs in Docker.

    Returns:
        typing.Optional[int]: The number of GPUs in Docker.
    """
    number_gpus = get_system_gpus()
    if number_gpus > 0:
        return f"0-{int(number_gpus) - 1}"
    return None


def get_system_gpu_arch() -> str:
    """Get the GPU architecture in the system.

    Returns:
        str: The GPU architecture in the system.

    Raises:
        Exception: If the GPU vendor is not NVIDIA or AMD.

    Note:
        AMD GPUs: "gfx908", "gfx906", "gfx90a"...
        NVIDIA GPUs: "H100", "A100", "V100"...
    """
    gpu_vendor = ""
    gpu_arch = ""

    try:
        gpu_vendor = get_gpu_vendor()
    except Exception as e:
        logger.error(f"Failed to get GPU vendor: {e}")
        sys.exit(1)

    if gpu_vendor == "NVIDIA":
        gpu_name = (
            subprocess.check_output(
                "nvidia-smi --query-gpu=gpu_name --format=csv,noheader", shell=True
            )
            .decode("utf-8")
            .strip()
        )
        # Define the regex pattern, matches Axxx, Hxxx, or Vxxx where x can be any digit
        pattern = r"([AHV])(\d{3})"
        match = re.search(pattern, gpu_name)
        # Extract the GPU architecture from the GPU name.
        if match:
            gpu_arch = match.group(0)  # Extract the matched pattern
        else:
            raise Exception(f"Failed to get GPU architecture of NVIDIA: {gpu_name}")
    elif gpu_vendor == "AMD":
        gpu_arch = (
            subprocess.check_output(
                "/opt/rocm/bin/rocminfo |grep -o -m 1 'gfx.*'", shell=True
            )
            .decode("utf-8")
            .strip()
        )
    else:
        raise Exception(f"Unsupported GPU vendor: {gpu_vendor}")

    logger.info(f"GPU vendor: {gpu_vendor}")
    logger.info(f"GPU architecture: {gpu_arch}")
    return gpu_arch


def get_gpu_docker_args() -> str:
    """Get the GPU Docker arguments based on the GPU vendor.

    Returns:
        str: The GPU Docker arguments.

    Raises:
        Exception: If the GPU vendor is not NVIDIA or AMD.
    """
    try:
        gpu_vendor = get_gpu_vendor()
    except Exception as e:
        logger.error(f"Failed to get GPU vendor: {e}")
        sys.exit(1)

    gpu_args = ""
    # Use all GPUs for docker run command.
    if gpu_vendor == "NVIDIA":
        gpu_args = f"--gpus all --shm-size 16g"
    elif gpu_vendor == "AMD":
        gpu_args = f"--device=/dev/kfd --device=/dev/dri --shm-size 16g"
    else:
        gpu_args = ""

    logger.debug(f"GPU Docker arguments: {gpu_args}")
    return gpu_args


def get_cpu_docker_args(request_cpus: typing.Optional[str] = None) -> str:
    """Get the CPU Docker arguments.

    Args:
        request_cpus (str): The requested CPUs, such as "0-3,10-15".

    Returns:
        str: The CPU Docker arguments.

    Note:
        --cpuset-cpus="0-3" specifies that the container can use CPUs 0 to 3.
    """
    cpu_args = ""
    if request_cpus:
        # Use the requested CPUs for docker run command.
        cpu_args = f'--cpuset-cpus="{request_cpus}"'
    else:
        # Use all CPUs for docker run command.
        cpu_args = f'--cpuset-cpus="0-{get_system_cpus() - 1}"'

    logger.debug(f"CPU Docker arguments: {cpu_args}")
    return cpu_args


def get_env_docker_args(run_envs: typing.Optional[typing.Dict] = None) -> str:
    """Get the environment Docker arguments.

    Args:
        run_envs (dict): The environment variables.

    Returns:
        str: The environment Docker arguments.
    """
    env_args = ""
    if run_envs:
        for key, value in run_envs.items():
            env_args += f"--env {key}={value} "

    logger.debug(f"Environment Docker arguments: {env_args}")
    return env_args


def get_mount_docker_args(
    mount_data_paths: typing.Optional[typing.List[typing.Dict]] = None,
) -> str:
    """Get the mount Docker arguments.

    Args:
        mount_data_paths (list): The mount data paths.

    Returns:
        str: The mount Docker arguments.

    Note:
        -v /host_path:/container_path:rw specifies that the host_path is mounted to the container_path in read-write mode.
        [ { "host_path": "/host_path", "container_path": "/container_path", "read_write": True }]
        -v /host_path:/container_path:ro specifies that the host_path is mounted to the container_path in read-only mode.
        [ { "host_path": "/host_path", "container_path": "/container_path", "read_write": False }]
    """
    mount_args = ""
    if mount_data_paths:
        for mount_data_path in mount_data_paths:
            mount_args += (
                f"-v {mount_data_path['host_path']}:{mount_data_path['container_path']}"
            )
            if (
                "read_write" in mount_data_path
                and mount_data_path["read_write"] == True
            ):
                mount_args += ":rw "
            else:
                mount_args += ":ro "

    logger.debug(f"Mount Docker arguments: {mount_args}")
    return mount_args


def get_base_docker(dockerfile: str) -> str:
    """Get the base Docker image.

    Args:
        dockerfile (str): The Dockerfile.

    Returns:
        str: The base Docker image.
    """
    with open(dockerfile, "r") as f:
        lines = f.readlines()

    for line in lines:
        if "ARG BASE_DOCKER" in line:
            return line.split("=")[-1].replace("\n", "").strip()

    return ""


def get_base_docker_sha(base_docker: str) -> str:
    """Get the base Docker image SHA.

    Args:
        base_docker (str): The base Docker image.

    Returns:
        str: The base Docker image SHA.
    """
    return (
        subprocess.check_output(
            f"docker inspect --format='{{{{.Id}}}}' {base_docker}", shell=True
        )
        .decode("utf-8")
        .strip()
    )


def get_host_name() -> str:
    """Get the host name.

    Returns:
        str: The host name.
    """
    host_name = os.uname().nodename
    logger.info(f"Host name: {host_name}")
    return host_name


def load_models() -> typing.List[typing.Dict]:
    """Load the models from the models.json file.

    Returns:
        typing.List[typing.Dict]: The models.
    """
    try:
        with open("models.json", "r") as f:
            models = json.load(f)
    except FileNotFoundError as e:
        logger.error(f"Failed to load models: {e}")
        sys.exit(1)

    logger.debug(f"Models: {models}")
    return models


def read_log_file(log_file: str) -> str:
    """Read the log file.

    Args:
        log_file (str): The log file.

    Returns:
        str: The log file content.
    """
    try:
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                log_content = f.read()
                return log_content
        except UnicodeDecodeError as e:
            with open(log_file, "rb") as f:
                log_content = f.read()
                return log_content.decode("utf-8", "ignore")
    except FileNotFoundError as e:
        logger.error(f"Failed to read log file: {e}")
        sys.exit(1)


def get_perf_metric(log_file: str) -> typing.Tuple[str, str]:
    """Parse the performance metric.

    Args:
        log_file (str): The log file.

    Returns:
        typing.Tuple[str, str]: The performance and metric.

    Raises:
        Exception: If the log file does not exist.
        Exception: If the log file is empty.
        Exception: If the log file does not contain performance.
    """
    # Initialize the variables.
    perf = ""
    metric = ""

    # Search 'performance:' and 'metric:' in the log file, if found, extract the values.
    if not os.path.exists(log_file):
        logger.error(f"Log file {log_file} does not exist")
        raise Exception(f"Log file {log_file} does not exist")

    # Check if the log file is empty.
    if os.stat(log_file).st_size == 0:
        logger.error(f"Log file {log_file} is empty")
        raise Exception(f"Log file {log_file} is empty")

    log_content = read_log_file(log_file)

    # Check if the log file contains 'performance:' and 'metric:'.
    if not re.search("performance:", log_content):
        logger.error(f"Log file {log_file} does not contain performance")
        raise Exception(f"Log file {log_file} does not contain performance")
    else:
        perf_regex = ".*performance:\\s*([+|-]?\d*[.]?\d*)\\s*.*\\s*"
        perf = re.search(perf_regex, log_content).group(1)
        metric_regex = ".*performance:\\s*[+|-]?\d*[.]?\d*\\s*(\w*)\\s*"
        metric = re.search(metric_regex, log_content).group(1)

    return perf, metric


def update_dict(d: typing.Dict, u: typing.Dict) -> typing.Dict:
    """Updates existing dictionary

    Args:
        d (dict): The existing dictionary.
        u (dict): The new dictionary.

    Returns:
        dict: The updated dictionary
    """
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = update_dict(d.get(k, {}), v)
        else:
            d[k] = v
    return d


# ==================================================================================================
# Performance CSV functions
# ==================================================================================================
def df_strip_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strips the columns of a dataframe

    Args:
        df (pd.DataFrame): The dataframe

    Returns:
        pd.DataFrame: The dataframe with stripped columns
    """
    df.columns = df.columns.str.strip()
    return df


def read_json(js: str) -> typing.Dict:
    """Reads a json file

    Args:
        js (str): The json file

    Returns:
        dict: The json file as a dictionary
    """
    with open(js, "r") as f:
        return json.load(f)


def flatten_tags(perf_entry: typing.Dict) -> None:
    """Flattens the tags to a string

    Args:
        perf_entry (dict): The performance entry

    Returns:
        None
    """
    if type(perf_entry["tags"]) == list:
        perf_entry["tags"] = ",".join(str(item) for item in perf_entry["tags"])


def perf_entry_df_to_csv(perf_entry: pd.DataFrame) -> None:
    """Writes the performance entry dataframe to a csv file

    Args:
        perf_entry (pd.DataFrame): The performance entry dataframe

    Returns:
        None
    """
    perf_entry.to_csv("perf_entry.csv", index=False)


def perf_entry_dict_to_csv(perf_entry: typing.Dict) -> None:
    """Writes the performance entry dictionary to a csv file

    Args:
        perf_entry (dict): The performance entry dictionary

    Returns:
        None
    """
    flatten_tags(perf_entry)
    js_df = pd.DataFrame(perf_entry, index=[0])
    perf_entry_df_to_csv(js_df)


def handle_single_result(perf_csv_df: pd.DataFrame, single_result: str) -> pd.DataFrame:
    """Handles the single result

    Args:
        perf_csv_df (pd.DataFrame): The performance csv dataframe.
        single_result (str): The single result json file.

    Returns:
        pd.DataFrame: The updated performance csv dataframe
    """
    single_result_json = read_json(single_result)
    perf_entry_dict_to_csv(single_result_json)
    perf_csv_df = pd.concat(
        [perf_csv_df, pd.DataFrame(single_result_json, index=[0])], ignore_index=True
    )
    return perf_csv_df


def handle_exception_result(
    perf_csv_df: pd.DataFrame, exception_result: str
) -> pd.DataFrame:
    """Handles the exception result

    Args:
        perf_csv_df (pd.DataFrame): The performance csv dataframe.
        exception_result (str): The exception result json file.

    Returns:
        pd.DataFrame: The updated performance csv dataframe
    """
    exception_result_json = read_json(exception_result)
    perf_entry_dict_to_csv(exception_result_json)
    perf_csv_df = pd.concat(
        [perf_csv_df, pd.DataFrame(exception_result_json, index=[0])], ignore_index=True
    )

    return perf_csv_df


def handle_multiple_results(
    perf_csv_df: pd.DataFrame, multiple_results: str, common_info: str, model_name: str
) -> pd.DataFrame:
    """Handles the multiple results

    Args:
        perf_csv_df (pd.DataFrame): The performance csv dataframe.
        multiple_results (str): The multiple results json file.
        common_info (str): The common information json file.
        model_name (str): The model name.

    Returns:
        pd.DataFrame: The updated performance csv dataframe

    Raises:
        RuntimeError: If the multiple results CSV file does not have three columns: model, performance, metric
        RuntimeError: If the multiple results CSV file is missing the model, performance, or metric column
    """
    # Check that the multiple results CSV has three columns and has the following format: model, performance, metric
    multiple_results_df = df_strip_columns(pd.read_csv(multiple_results))
    multiple_results_header = multiple_results_df.columns.tolist()

    # Check that the multiple results CSV has three columns
    if len(multiple_results_header) != 3:
        raise RuntimeError(
            "Multiple Results CSV file must have three columns: model, performance, metric"
        )

    # Check that the multiple results CSV has the model, performance, and metric columns
    headings = ["model", "performance", "metric"]
    for heading in headings:
        if not (heading in multiple_results_header):
            raise RuntimeError(
                "Multiple Results CSV file is missing the " + heading + " column"
            )

    # Read the common information JSON file and flatten the tags
    common_info_json = read_json(common_info)
    # Flatten the tags
    flatten_tags(common_info_json)

    # Create a new dataframe to store the final results
    final_multiple_results_df = pd.DataFrame()

    # Add results to perf.csv
    for r in multiple_results_df.to_dict(orient="records"):
        row = common_info_json
        row["model"] = model_name + "_" + str(r["model"])
        row["performance"] = r["performance"]
        row["metric"] = r["metric"]

        if r["performance"] is not None and pd.notna(r["performance"]):
            row["status"] = "SUCCESS"
        else:
            row["status"] = "FAILURE"

        # Check that the number of columns in the row is the same as the number of columns in the CSV
        # assert perf_csv_df.columns.size == len(row)
        # Add the row to the final results dataframe
        final_multiple_results_df = pd.concat(
            [final_multiple_results_df, pd.DataFrame(row, index=[0])], ignore_index=True
        )

    # Reorder the columns to match the perf.csv
    final_multiple_results_df = final_multiple_results_df[perf_csv_df.columns]
    # Write the final results to a CSV file
    perf_entry_df_to_csv(final_multiple_results_df)
    # Concatenate the final results to the perf.csv
    perf_csv_df = pd.concat([perf_csv_df, final_multiple_results_df])
    return perf_csv_df


def update_perf_csv(
    single_result: typing.Optional[str] = None,
    exception_result: typing.Optional[str] = None,
    failed_result: typing.Optional[str] = None,
    multiple_results: typing.Optional[str] = None,
    perf_csv: str = "perf.csv",
    model_name: typing.Optional[str] = None,
    common_info: typing.Optional[str] = None,
) -> None:
    """Updates the performance csv file with the new results

    Args:
        single_result (str): The single result json file.
        exception_result (str): The exception result json file.
        failed_result (str): The failed result json file.
        multiple_results (str): The multiple results json file.
        perf_csv (str): The performance csv file.
        model_name (str): The model name.
        common_info (str): The common information.

    Returns:
        None
    """
    # Check that the perf.csv exists
    if not os.path.exists(perf_csv):
        columns = [
            "pipeline",
            "model",
            "tags",
            "args",
            "docker_file",
            "base_docker",
            "docker_sha",
            "docker_image",
            "machine_name",
            "host_os",
            "gpu_architecture",
            "n_gpus",
            "training_precision",
            "performance",
            "metric",
            "status",
            "build_duration",
            "test_duration",
            "git_commit",
            "relative_change",
        ]
        perf_csv_df = pd.DataFrame(columns=columns)
    else:
        # Read the perf.csv
        perf_csv_df = df_strip_columns(pd.read_csv(perf_csv))
    logger.info(perf_csv_df)
    
    # Handle the results
    if multiple_results:
        perf_csv_df = handle_multiple_results(
            perf_csv_df, multiple_results, common_info, model_name
        )
    elif single_result:
        perf_csv_df = handle_single_result(perf_csv_df, single_result)
    elif exception_result:
        perf_csv_df = handle_exception_result(perf_csv_df, exception_result)
    else:
        raise RuntimeError(
            "At least one of the following must be provided: single_result, exception_result, failed_result, multiple_results"
        )

    logger.info(f"Updated performance dataframe:\n{perf_csv_df}")

    # Note that this file will also generate a perf_entry.csv regardless of the output file args.
    perf_csv_df.to_csv(perf_csv, index=False)
    logger.info(f"Wrote updated performance CSV to {perf_csv}")
