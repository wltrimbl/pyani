# Copyright 2013-2015, The James Hutton Insitute
# Author: Leighton Pritchard
#
# This code is part of the pyani package, and is governed by its licence.
# Please see the LICENSE file that should have been included as part of
# this package.

"""Code to run a set of command-line jobs using SGE/Grid Engine

For parallelisation on multi-node system, we use some custom code to submit
jobs.
"""

from collections import defaultdict

from . import pyani_config

import os

class JobGroup:
  """ Class that stores a group of jobs, permitting parameter sweeps."""
  def __init__(self, name, command, queue=None, arguments={}):
    """ Instantiate a JobGroup object.  JobGroups allow for the use of
    combinatorial parameter sweeps by using the 'command' and 'arguments'
    arguments.  
    
    - name              String, the JobGroup name
    - command           String, the command to be run, with arguments
                        specified
    - queue             String, the queue for SGE to use
    - arguments         Dictionary, the values for each parameter as
                        lists of strings, keyed by an identifier for
                        the command string
     
    For example, to use a command 'my_cmd' with the arguments
    '-foo' and '-bar' having values 1, 2, 3, 4 and 'a', 'b', 'c', 'd' in
    all combinations, respectively, you would pass
    command='my_cmd $SGE_TASK_ID -foo $fooargs -bar $barargs'
    arguments='{'fooargs': ['1','2','3','4'],
                'barargs': ['a','b','c','d']}
    """
    self.name = name                  # Set JobQueue name
    self.queue = queue                # Set SGE queue to request
    self.command = command            # Set command string
    self.dependencies = []            # Create empty list for dependencies
    self.submitted = True             # Set submitted Boolean
    self.arguments = arguments        # Dictionary of arguments for command
    self.generate_script()             # Make the SGE script for the parameter
                                      # sweep/array

  def generate_script(self):
    """Create the SGE script that will run the jobs in the JobGroup, with the
    passed arguments.
    """
    self.script = ""        # Holds the script string
    total = 1               # total number of jobs in this group

    # for now, SGE_TASK_ID becomes TASK_ID, but we base it at zero
    self.script += """let "TASK_ID=$SGE_TASK_ID - 1"\n"""

    # build the array definitions
    for key in self.arguments.keys():
      values = self.arguments[key]
      line = ("%s_ARRAY=( " % (key))
      for value in values:
        line += value
        line += " "
      line += " )\n"
      self.script += line
      total *= len(values)
    self.script += "\n"

    # now, build the decoding logic in the script
    for key in self.arguments.keys():
      count = len(self.arguments[key])
      self.script += """let "%s_INDEX=$TASK_ID %% %d"\n""" % ( key, count )
      self.script += """%s=${%s_ARRAY[$%s_INDEX]}\n""" % ( key, key, key )
      self.script += """let "TASK_ID=$TASK_ID / %d"\n""" % ( count )

    # now, add the command to run the job
    self.script += "\n"
    self.script += self.command
    self.script += "\n"

    # set the number of tasks in this group
    self.tasks = total


  def add_dependency(self, job):
    """Add the passed job to the dependency list for this JobGroup.  This
    JobGroup should not execute until all dependent jobs are completed
    
    - job         Job, job to be added to the JobGroup's dependency list
    """
    self.dependencies.append(job)


  def remove_dependency(self, job):
    """ Remove the passed job from this JobGroup's dependency list

    - job         Job, job to be removed from the JobGroup's dependency list
    """
    self.dependencies.remove(job)
    

  def wait( self ):
    """Wait for a defined period."""
    finished = False
    interval = 5
    while not finished:
      time.sleep(interval)
      interval = min( 2 * interval, 60 )
      finished = os.system("qstat -j %s > /dev/null" % (self.name))


# Run a job dependency graph, with SGE
def run_dependency_graph(jobgraph, verbose=False, logger=None):
    """Creates and runs GridEngine scripts for jobs based on the passed
    jobgraph.
    
    - jobgraph - list of jobs, which may have dependencies.
    - verbose - flag for multiprocessing verbosity
    - logger - a logger module logger (optional)

    The strategy here is to loop over each job in the list of jobs (jobgraph),
    and create/populate a series of Sets of commands, to be run in
    reverse order with multiprocessing_run as asynchronous pools.
    
    The strategy here is to loop over each job in the dependency graph, and
    add the job to a new list of jobs, swapping out the Job dependency for
    the name of the Job on which it depends.
    """
    jobset = set()
    for job in jobgraph:
        jobset = populate_jobset(job, jobset, depth=1)
    joblist = list(jobset)

    # Try to be informative by telling the user what jobs will run
    dep_count = 0  # how many dependencies are there
    if logger:
        logger.info("Jobs to run with scheduler")
        for job in joblist:
            logger.info("{0}: {1}".format(job.name, job.command))
            if len(job.dependencies):
                dep_count += len(job.dependencies)
                for dep in job.dependencies:
                    logger.info("\t[^ depends on: %s]" % dep.name)
    logger.info("There are %d job dependencies" % dep_count)

    # If there are no dependencies, place all jobs in JobGroups
    # This should only happen for MUMmer jobs
    if dep_count == 0:
        arglists = defaultdict(list)
        for job in joblist:
            cmd, args = job.split(' ', 1)
            arglists[cmd].append(args)
    jobgroups = []
    for cmd, arglist in list(arglists.items()):
        jg = JobGroup(cmd, cmd, arguments=arglist)
    joblist = jobgroups

    # If there are no job dependencies, we can use an array (or series of
    # arrays) to schedule our jobs. This cuts down on problems with long
    # job lists choking up the queue.

    # Send jobs to scheduler
    logger.info("Running jobs with scheduler...")
    build_and_submit_jobs(os.curdir, joblist)
    logger.info("Waiting for SGE-submitted jobs to finish (polling)")
    for job in joblist:
        job.wait()
    

def populate_jobset(job, jobset, depth):
    """ Creates a set of jobs, containing jobs at difference depths of the
    dependency tree, retaining dependencies as strings, not Jobs.
    """
    jobset.add(job)
    if len(job.dependencies) == 0:
        return jobset
    for j in job.dependencies:
        jobset = populate_jobset(j, jobset, depth+1)
    return jobset
        

def build_directories(root_dir):
  """Constructs the subdirectories output, stderr, stdout, and jobs in the
  passed root directory. These subdirectories have the following roles:

      jobs             Stores the scripts for each job
      stderr           Stores the stderr output from SGE
      stdout           Stores the stdout output from SGE
      output           Stores output (if the scripts place the output here)

  - root_dir   Path to the top-level directory for creation of subdirectories
  """
  # If the root directory doesn't exist, create it
  if not os.path.exists(root_dir):
      os.mkdir(root_dir)

  # Create subdirectories
  directories = [os.path.join(root_dir, subdir) for subdir in
                 ("output", "stderr", "stdout", "jobs")]
  [os.mkdir(dirname) for dirname in directories if not os.path.exists(dirname)]


def build_job_scripts(root_dir, jobs):
  """Constructs the script for each passed Job in the jobs iterable

  - root_dir      Path to output directory
  """
  # Loop over the job list, creating each job script in turn, and then adding
  # scriptPath to the Job object
  for job in jobs:
      scriptPath = os.path.join(root_dir, "jobs", job.name)
      scriptFile = file(scriptPath, "w")
      scriptFile.write("#!/bin/sh\n#$ -S /bin/bash\n%s\n" % job.script)
      scriptFile.close()
      job.scriptPath = scriptPath


def extract_submittable_jobs(waiting):
  """Obtain a list of jobs that are able to be submitted from the passed
  list of pending jobs

  - waiting           List of Job objects
  """
  submittable = set()            # Holds jobs that are able to be submitted
  # Loop over each job, and check all the subjobs in that job's dependency
  # list.  If there are any, and all of these have been submitted, then
  # append the job to the list of submittable jobs.
  for job in waiting:
      unsatisfied = sum([(subjob.submitted is False) for subjob in \
                         job.dependencies])
      if 0 == unsatisfied:
          submittable.add(job)
  return list(submittable)


def submit_safe_jobs(root_dir, jobs):
  """Submit the passed list of jobs to the Grid Engine server, using the passed
  directory as the root for scheduler output.

  - root_dir      Path to output directory
  - jobs          Iterable of Job objects
  """
  # Loop over each job, constructing the SGE command-line based on job settings
  for job in jobs:
      job.out = os.path.join(root_dir, "stdout")
      job.err = os.path.join(root_dir, "stderr")
      # Add the job name, current working directory, and SGE stdout and stderr
      # directories to the SGE command line
      args = " -N %s " % (job.name)
      args += " -cwd "
      args += " -o %s -e %s " % (job.out, job.err)
      # If a queue is specified, add this to the SGE command line
      if job.queue != None and job.queue in local_queues:
          args += local_queues[job.queue]
          #args += "-q %s " % job.queue
      # If there are dependencies for this job, hold the job until they are
      # complete
      if len(job.dependencies) > 0:
          args += "-hold_jid "
          for dep in job.dependencies:
              args += dep.name + ","
          args = args[:-1]
      # Build the qsub SGE commandline (passing local environment)
      qsubcmd = ("%s -V %s %s" % \
                 (pyani_config.QSUB_DEFAULT, args, job.scriptPath)) 
      #print qsubcmd                   # Show the command to the user
      os.system(qsubcmd)               # Run the command
      job.submitted = True             # Set the job's submitted flag to True


def submit_jobs(root_dir, jobs):
  """ Submit each of the passed jobs to the SGE server, using the passed
  directory as root for SGE output.

  - root_dir       Path to output directory
  - jobs           List of Job objects
  """
  waiting = list(jobs)                 # List of jobs still to be done
  # Loop over the list of pending jobs, while there still are any
  while len(waiting) > 0:
      # extract submittable jobs
      submittable = extract_submittable_jobs(waiting)
      # run those jobs
      submit_safe_jobs(root_dir, submittable)
      # remove those from the waiting list
      for job in submittable:
          waiting.remove(job)


def build_and_submit_jobs(root_dir, jobs):
  """Submits the passed iterable of Job objects to SGE, placing SGE's output in
  the passed root directory

  - root_dir   Root directory for SGE and job output

  - jobs       List of Job objects, describing each job to be submitted
  """
  # If the passed set of jobs is not a list, turn it into one.  This makes the
  # use of a single JobGroup a little more intutitive
  if type(jobs) != type([1]):
      jobs = [jobs]
    
  # Build and submit the passed jobs
  build_directories(root_dir)       # build all necessary directories
  build_job_scripts(root_dir, jobs) # build job scripts
  submit_jobs(root_dir, jobs)       # submit the jobs to SGE
